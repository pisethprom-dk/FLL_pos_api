# v1.4.0 — the inventory rules that matter
import io
from datetime import timedelta
from decimal import Decimal as D
from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from catalogue.models import Brand, Category, Product, Unit
from core.exceptions import DomainError, InsufficientStockError, PostedDocumentError
from inventory.models import (
    Adjustment,
    AdjustmentLine,
    AdjustmentReason as R,
    DocStatus,
    StockCount,
    StockIn,
    StockInLine,
    StockMovement,
)
from inventory.services import (
    issue,
    lock_products,
    post_adjustment,
    post_count,
    post_stock_in,
    record_counts,
    ref_for,
    reverse_document,
    start_count,
)
from partners.models import ProductSupplier, Supplier, SupplierType
from users.models import Role, User


class InventoryTestCase(TestCase):
    def setUp(self):
        call_command("seed", stdout=StringIO())
        self.admin = User.objects.create_user(
            username="owner", password="owner-pass-99", full_name="Bopha Ly",
            role=Role.ADMIN,
        )
        self.seller = User.objects.create_user(
            username="dara", password="counter-pass-99", full_name="Dara Meas",
            role=Role.SELLER,
        )
        self.hand = Category.objects.create(code="CAT-02", name="Hand tools")
        self.wrenches = Category.objects.create(code="CAT-0201", name="Wrenches", parent=self.hand)
        self.fixings = Category.objects.create(code="CAT-03", name="Fixings")
        self.pcs = Unit.objects.get(code="PCS")
        self.spanner = self.product("TL-0240", "Combination spanner 12mm", self.wrenches, "B1-2")
        self.spanner_set = self.product("TL-0241", "Combination spanner set", self.wrenches, "B1-3")
        self.screw = self.product("FX-0302", "Wood screw 4×40mm", self.fixings, "C1-4")
        self.key_cutting = self.product("SV-0001", "Key cutting", self.fixings, track_stock=False)
        self.supplier = Supplier.objects.create(
            name="Total Tools (Cambodia)", supplier_type=SupplierType.DISTRIBUTOR
        )

    def product(self, code, name, category, shelf="", **kw):
        return Product.objects.create(
            code=code, name=name, category=category, unit=self.pcs,
            shelf_location=shelf, retail_price=D("3.20"), wholesale_price=D("2.70"), **kw,
        )

    def stock_in(self, *lines, post=True, **kw):
        """lines: (product, packs, cost per pack[, pack size])"""
        doc = StockIn.objects.create(supplier=kw.pop("supplier", self.supplier), created_by=self.admin, **kw)
        for product, packs, cost, *size in lines:
            StockInLine(
                document=doc, product=product, packs=D(packs), pack_cost=D(cost),
                pack_size=D(size[0]) if size else D("1"),
            ).save()
        return post_stock_in(doc, self.admin) if post else doc

    def adjust(self, reason, *lines, supplier=None, post=True):
        """lines: (product, quantity[, unit cost])"""
        doc = Adjustment.objects.create(reason=reason, supplier=supplier, created_by=self.admin)
        for product, qty, *cost in lines:
            AdjustmentLine(
                document=doc, product=product, quantity=D(qty),
                unit_cost=D(cost[0]) if cost else None,
            ).save()
        return post_adjustment(doc, self.admin) if post else doc

    def position(self, product):
        product.refresh_from_db()
        return product.qty_on_hand, product.avg_cost

    def movements_of(self, doc):
        return StockMovement.objects.filter(doc_type=doc.DOC_TYPE, doc_id=doc.pk)

    def _as(self, user, password):
        self.client = APIClient()
        res = self.client.post(
            reverse("auth-login"),
            {"username": user.username, "password": password},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")


class StockInTests(InventoryTestCase):
    def test_a_draft_changes_nothing(self):
        self.stock_in((self.spanner, "10", "1.85"), post=False)
        self.assertEqual(self.position(self.spanner), (D("0"), D("0")))
        self.assertFalse(StockMovement.objects.exists())

    def test_posting_receives_stock_and_writes_the_ledger(self):
        doc = self.stock_in((self.spanner, "10", "1.85"))
        self.assertEqual(doc.status, DocStatus.POSTED)
        self.assertEqual(self.position(self.spanner), (D("10"), D("1.85")))
        m = self.movements_of(doc).get()
        self.assertEqual((m.quantity, m.qty_after, m.avg_cost_after), (D("10"), D("10"), D("1.85")))
        self.assertEqual(m.doc_number, doc.number)

    def test_the_average_moves_with_each_purchase(self):
        self.stock_in((self.spanner, "10", "1.00"))
        self.stock_in((self.spanner, "10", "2.00"))
        self.assertEqual(self.position(self.spanner), (D("20"), D("1.5")))

    def test_cartons_convert_to_units_and_the_average_takes_the_money_paid(self):
        # $26.40 over a carton of 23 does not divide evenly.
        doc = self.stock_in((self.screw, "1", "26.40", "23"))
        line = doc.lines.get()
        self.assertEqual((line.quantity, line.unit_cost, line.line_total), (D("23"), D("1.1478"), D("26.40")))
        self.assertEqual(self.movements_of(doc).get().value, D("26.40"))
        self.assertEqual(self.position(self.screw), (D("23"), D("1.1478")))

    def test_a_posted_stock_in_is_frozen(self):
        doc = self.stock_in((self.spanner, "10", "1.85"))
        doc.supplier_ref = "changed"
        with self.assertRaises(PostedDocumentError):
            doc.save()
        with self.assertRaises(PostedDocumentError):
            doc.delete()
        with self.assertRaises(PostedDocumentError):
            doc.lines.get().save()

    def test_a_deleted_draft_leaves_a_gap_in_the_numbers(self):
        draft = self.stock_in((self.spanner, "1", "1"), post=False)
        self.assertEqual(draft.number, "GRN-000001")
        draft.delete()
        self.assertEqual(self.stock_in((self.spanner, "1", "1"), post=False).number, "GRN-000002")

    def test_services_and_future_dates_are_refused(self):
        with self.assertRaises(DomainError):
            self.stock_in((self.key_cutting, "1", "1"))
        with self.assertRaises(DomainError):
            self.stock_in((self.spanner, "1", "1"), doc_date=timezone.localdate() + timedelta(days=1))

    def test_the_ledger_is_append_only(self):
        doc = self.stock_in((self.spanner, "10", "1.85"))
        m = self.movements_of(doc).get()
        with self.assertRaises(DomainError):
            m.save()
        with self.assertRaises(DomainError):
            m.delete()


class AdjustmentTests(InventoryTestCase):
    def test_damage_goes_out_at_the_average_and_leaves_it_alone(self):
        self.stock_in((self.spanner, "10", "1.00"))
        self.stock_in((self.spanner, "10", "2.00"))
        doc = self.adjust(R.DAMAGE, (self.spanner, "4"))
        self.assertEqual(self.position(self.spanner), (D("16"), D("1.5")))
        line = doc.lines.get()
        self.assertEqual((line.unit_cost, line.value), (D("1.5"), D("-6.00")))

    def test_stock_never_goes_below_zero_and_nothing_half_posts(self):
        self.stock_in((self.spanner, "5", "1.00"))
        doc = self.adjust(R.LOSS, (self.spanner, "2"), (self.spanner_set, "1"), post=False)
        with self.assertRaises(InsufficientStockError):
            post_adjustment(doc, self.admin)
        self.assertEqual(self.position(self.spanner), (D("5"), D("1")))
        doc.refresh_from_db()
        self.assertEqual(doc.status, DocStatus.DRAFT)
        self.assertFalse(self.movements_of(doc).exists())

    def test_only_an_opening_balance_may_type_a_cost(self):
        self.stock_in((self.spanner, "5", "1.00"))
        with self.assertRaises(DomainError):
            self.adjust(R.DAMAGE, (self.spanner, "1", "9.99"))

    def test_opening_balance_is_per_product(self):
        self.adjust(R.OPENING_BALANCE, (self.spanner, "10", "1.85"))
        self.assertEqual(self.position(self.spanner), (D("10"), D("1.85")))
        with self.assertRaises(DomainError):
            self.adjust(R.OPENING_BALANCE, (self.spanner, "5", "2.00"))
        # The set was forgotten at go-live; other documents have been posted
        # since, but the ledger has never moved the set itself.
        self.adjust(R.OPENING_BALANCE, (self.spanner_set, "4", "18.40"))
        self.assertEqual(self.position(self.spanner_set), (D("4"), D("18.4")))

    def test_an_opening_balance_can_be_redone_after_reversal(self):
        first = self.adjust(R.OPENING_BALANCE, (self.spanner, "10", "1.85"))
        reverse_document(first, self.admin, "Wrong cost")
        self.adjust(R.OPENING_BALANCE, (self.spanner, "10", "1.90"))
        self.assertEqual(self.position(self.spanner), (D("10"), D("1.9")))

    def test_supplier_is_set_for_returns_and_replacements_only(self):
        self.stock_in((self.spanner, "5", "1.00"))
        with self.assertRaises(DomainError):
            self.adjust(R.RETURN_TO_SUPPLIER, (self.spanner, "1"))
        with self.assertRaises(DomainError):
            self.adjust(R.DAMAGE, (self.spanner, "1"), supplier=self.supplier)

    def test_a_supplier_replacement_comes_back_at_the_current_average(self):
        self.stock_in((self.spanner, "10", "2.00"))
        self.adjust(R.RETURN_TO_SUPPLIER, (self.spanner, "2"), supplier=self.supplier)
        self.assertEqual(self.position(self.spanner), (D("8"), D("2")))
        doc = self.adjust(R.SUPPLIER_REPLACEMENT, (self.spanner, "2"), supplier=self.supplier)
        self.assertEqual(self.position(self.spanner), (D("10"), D("2")))
        self.assertEqual(doc.lines.get().value, D("4.00"))


class CountTests(InventoryTestCase):
    def test_a_count_lists_the_category_and_its_children(self):
        count = start_count(self.hand, self.admin)
        self.assertEqual(
            sorted(count.lines.values_list("product__code", flat=True)), ["TL-0240", "TL-0241"]
        )

    def test_blank_lines_are_skipped_not_zeroed(self):
        self.stock_in((self.spanner, "10", "1"), (self.spanner_set, "5", "1"))
        count = start_count(self.wrenches, self.admin)
        line = count.lines.get(product=self.spanner)
        record_counts(count, [{"line": line.pk, "counted_qty": D("8")}])
        post_count(count, self.admin)
        self.assertEqual(self.position(self.spanner)[0], D("8"))
        self.assertEqual(self.position(self.spanner_set)[0], D("5"))

    def test_a_sale_made_after_a_line_is_counted_is_not_a_difference(self):
        self.stock_in((self.spanner, "10", "1"))
        count = start_count(self.wrenches, self.admin)
        line = count.lines.get(product=self.spanner)
        record_counts(count, [{"line": line.pk, "counted_qty": D("10")}])
        # Three sold at the till while the count is still open.
        with transaction.atomic():
            product = lock_products([self.spanner.pk])[self.spanner.pk]
            issue(product, D("3"), ref_for(count, self.admin))
        post_count(count, self.admin)
        line.refresh_from_db()
        self.assertEqual(line.difference, D("0"))
        self.assertEqual(self.position(self.spanner)[0], D("7"))

    def test_gains_and_losses_move_at_the_current_average(self):
        self.stock_in((self.spanner, "10", "2.00"), (self.spanner_set, "4", "5.00"))
        count = start_count(self.wrenches, self.admin)
        lines = {l.product_id: l for l in count.lines.all()}
        record_counts(count, [
            {"line": lines[self.spanner.pk].pk, "counted_qty": D("12")},
            {"line": lines[self.spanner_set.pk].pk, "counted_qty": D("3")},
        ])
        post_count(count, self.admin)
        self.assertEqual(self.position(self.spanner), (D("12"), D("2")))
        self.assertEqual(self.position(self.spanner_set), (D("3"), D("5")))
        values = dict(count.lines.values_list("product__code", "value"))
        self.assertEqual(values, {"TL-0240": D("4.00"), "TL-0241": D("-5.00")})

    def test_a_product_is_on_one_open_count_at_a_time(self):
        start_count(self.wrenches, self.admin)
        with self.assertRaises(DomainError):
            start_count(self.hand, self.admin)

    def test_a_count_with_nothing_counted_cannot_post(self):
        count = start_count(self.wrenches, self.admin)
        with self.assertRaises(DomainError):
            post_count(count, self.admin)


class ReversalTests(InventoryTestCase):
    def test_reversing_a_stock_in_restores_quantity_and_average(self):
        self.stock_in((self.spanner, "10", "1.00"))
        second = self.stock_in((self.spanner, "10", "2.00"))
        reversal = reverse_document(second, self.admin, "Keyed twice")
        self.assertEqual(self.position(self.spanner), (D("10"), D("1")))
        self.assertEqual(reversal.reverses, second)
        self.assertTrue(all(m.is_reversal for m in self.movements_of(reversal)))

    def test_a_reversal_is_refused_once_the_stock_has_gone(self):
        doc = self.stock_in((self.spanner, "10", "1.00"))
        self.adjust(R.DAMAGE, (self.spanner, "8"))
        with self.assertRaises(InsufficientStockError):
            reverse_document(doc, self.admin, "Too late")
        self.assertEqual(self.position(self.spanner), (D("2"), D("1")))
        self.assertFalse(StockIn.objects.filter(reverses=doc).exists())

    def test_reversed_once_and_a_reversal_never(self):
        doc = self.stock_in((self.spanner, "10", "1.00"))
        with self.assertRaises(DomainError):
            reverse_document(doc, self.admin, "  ")
        reversal = reverse_document(doc, self.admin, "Wrong supplier")
        with self.assertRaises(DomainError):
            reverse_document(doc, self.admin, "Again")
        with self.assertRaises(DomainError):
            reverse_document(reversal, self.admin, "Undo the undo")

    def test_reversing_a_write_off_brings_stock_back_at_its_stamped_cost(self):
        self.stock_in((self.spanner, "10", "1.00"))
        damage = self.adjust(R.DAMAGE, (self.spanner, "2"))
        self.stock_in((self.spanner, "8", "3.00"))
        self.assertEqual(self.position(self.spanner), (D("16"), D("2")))
        reverse_document(damage, self.admin, "Found undamaged")
        # Back in at $1.00, the cost it went out at — not today's $2.00.
        self.assertEqual(self.position(self.spanner), (D("18"), D("1.8889")))


class RecomputeTests(InventoryTestCase):
    def test_the_ledger_replays_to_the_figures_that_were_written(self):
        self.stock_in((self.screw, "1", "26.40", "23"))
        self.stock_in((self.spanner, "10", "1.00"), (self.spanner, "7", "1.37"))
        damage = self.adjust(R.DAMAGE, (self.spanner, "3"))
        self.stock_in((self.spanner, "5", "2.11"))
        reverse_document(damage, self.admin, "Found")
        self.adjust(R.SHOP_USE, (self.screw, "4"))
        call_command("recompute_stock", "--check", stdout=StringIO())

    def test_check_finds_drift_and_recompute_mends_it(self):
        self.stock_in((self.spanner, "10", "1.85"))
        Product.objects.filter(pk=self.spanner.pk).update(qty_on_hand=99, avg_cost=0)
        with self.assertRaises(CommandError):
            call_command("recompute_stock", "--check", stdout=StringIO())
        call_command("recompute_stock", stdout=StringIO())
        self.assertEqual(self.position(self.spanner), (D("10"), D("1.85")))
        call_command("recompute_stock", "--check", stdout=StringIO())


class InventoryApiTests(InventoryTestCase):
    def test_the_stock_area_is_closed_to_sellers(self):
        self._as(self.seller, "counter-pass-99")
        for url in ["stock-ins", "adjustments", "counts", "movements"]:
            self.assertEqual(self.client.get(f"/api/inventory/{url}/").status_code, 403, url)

    def test_draft_post_then_frozen(self):
        self._as(self.admin, "owner-pass-99")
        res = self.client.post("/api/inventory/stock-ins/", {
            "supplier": self.supplier.pk, "supplier_ref": "TT-88204",
            "lines": [{"product": self.spanner.pk, "packs": "10", "pack_cost": "1.85"}],
        }, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["status"], "DRAFT")
        url = f"/api/inventory/stock-ins/{res.data['id']}/"

        res = self.client.post(url + "post/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["status"], "POSTED")
        self.assertEqual(res.data["total"], "18.50")

        self.assertEqual(self.client.patch(url, {"note": "x"}, format="json").status_code, 400)
        self.assertEqual(self.client.delete(url).status_code, 400)

    def test_a_stray_post_to_the_document_url_does_not_post_it(self):
        doc = self.stock_in((self.spanner, "1", "1"), post=False)
        self._as(self.admin, "owner-pass-99")
        res = self.client.post(f"/api/inventory/stock-ins/{doc.pk}/", {}, format="json")
        self.assertEqual(res.status_code, 405)
        doc.refresh_from_db()
        self.assertEqual(doc.status, DocStatus.DRAFT)

    def test_the_pack_defaults_from_the_supplier_link(self):
        ProductSupplier.objects.create(
            product=self.screw, supplier=self.supplier,
            pack_unit=Unit.objects.get(code="CTN"), pack_size=D("24"),
        )
        self._as(self.admin, "owner-pass-99")
        res = self.client.post("/api/inventory/stock-ins/", {
            "supplier": self.supplier.pk,
            "lines": [{"product": self.screw.pk, "packs": "2", "pack_cost": "26.40"}],
        }, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        line = res.data["lines"][0]
        self.assertEqual(
            (line["pack_unit_name"], line["pack_size"], line["quantity"], line["unit_cost"]),
            ("Carton", "24.00", "48.00", "1.1000"),
        )

    def test_a_line_that_gives_its_pack_keeps_it(self):
        ProductSupplier.objects.create(
            product=self.screw, supplier=self.supplier,
            pack_unit=Unit.objects.get(code="CTN"), pack_size=D("24"),
        )
        self._as(self.admin, "owner-pass-99")
        res = self.client.post("/api/inventory/stock-ins/", {
            "supplier": self.supplier.pk,
            "lines": [{"product": self.screw.pk, "pack_unit": None, "pack_size": "1",
                       "packs": "5", "pack_cost": "1.15"}],
        }, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        line = res.data["lines"][0]
        self.assertEqual((line["pack_unit"], line["pack_size"], line["quantity"]), (None, "1.00", "5.00"))

    def test_each_line_names_its_brand_and_category(self):
        Product.objects.filter(pk=self.spanner.pk).update(brand=Brand.objects.create(name="Total"))
        self.stock_in((self.spanner, "10", "1.85"))
        self._as(self.admin, "owner-pass-99")
        named = lambda lines: [  # noqa: E731
            (l["product_code"], l["brand_name"], l["category_name"]) for l in lines
        ]
        doc = self.stock_in((self.spanner, "2", "1.85"), (self.screw, "1", "2.00"), post=False)
        self.assertEqual(
            named(self.client.get(f"/api/inventory/stock-ins/{doc.pk}/").data["lines"]),
            [("TL-0240", "Total", "Wrenches"), ("FX-0302", None, "Fixings")],
        )
        doc = self.adjust(R.DAMAGE, (self.spanner, "1"), post=False)
        self.assertEqual(
            named(self.client.get(f"/api/inventory/adjustments/{doc.pk}/").data["lines"]),
            [("TL-0240", "Total", "Wrenches")],
        )
        res = self.client.post("/api/inventory/counts/", {"category": self.wrenches.pk}, format="json")
        self.assertEqual(
            sorted(named(res.data["lines"])),
            [("TL-0240", "Total", "Wrenches"), ("TL-0241", None, "Wrenches")],
        )

    def test_an_adjustment_totals_its_value_once_posted(self):
        self.stock_in((self.spanner, "10", "1.85"))
        doc = self.adjust(R.DAMAGE, (self.spanner, "2"), post=False)
        self._as(self.admin, "owner-pass-99")
        url = f"/api/inventory/adjustments/{doc.pk}/"
        self.assertIsNone(self.client.get(url).data["total"])
        res = self.client.post(url + "post/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["total"], "-3.70")

    def test_the_api_refuses_a_typed_cost_on_a_write_off(self):
        self._as(self.admin, "owner-pass-99")
        res = self.client.post("/api/inventory/adjustments/", {
            "reason": "DAMAGE",
            "lines": [{"product": self.spanner.pk, "quantity": "1", "unit_cost": "1.00"}],
        }, format="json")
        self.assertEqual(res.status_code, 400)

    def test_a_count_is_blind_until_posted(self):
        self.stock_in((self.spanner, "10", "1"))
        self._as(self.admin, "owner-pass-99")
        res = self.client.post("/api/inventory/counts/", {"category": self.wrenches.pk}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        url = f"/api/inventory/counts/{res.data['id']}/"
        line = next(l for l in res.data["lines"] if l["product"] == self.spanner.pk)
        self.assertIsNone(line["expected_qty"])

        res = self.client.post(url + "record/", {"lines": [{"line": line["id"], "counted_qty": "9"}]}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["lines_counted"], 1)
        self.assertTrue(all(l["expected_qty"] is None and l["difference"] is None for l in res.data["lines"]))

        self.assertIsNone(res.data["differences"])
        self.assertIsNone(res.data["total"])

        res = self.client.post(url + "post/")
        self.assertEqual(res.status_code, 200, res.data)
        counted = next(l for l in res.data["lines"] if l["product"] == self.spanner.pk)
        self.assertEqual((counted["expected_qty"], counted["difference"]), ("10.00", "-1.00"))
        self.assertEqual((res.data["differences"], res.data["total"]), (1, "-1.00"))

    def test_the_movement_list_is_a_stock_card(self):
        self.stock_in((self.spanner, "10", "1.00"))
        self.adjust(R.DAMAGE, (self.spanner, "2"))
        self._as(self.admin, "owner-pass-99")
        res = self.client.get(f"/api/inventory/movements/?product={self.spanner.pk}")
        rows = [(r["quantity"], r["qty_after"]) for r in res.data["results"]]
        self.assertEqual(rows, [("10.00", "10.00"), ("-2.00", "8.00")])

    def test_reverse_through_the_api(self):
        doc = self.stock_in((self.spanner, "10", "1.00"))
        self._as(self.admin, "owner-pass-99")
        url = f"/api/inventory/stock-ins/{doc.pk}/reverse/"
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 400)
        res = self.client.post(url, {"note": "Keyed twice"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["reverses_number"], doc.number)
        self.assertEqual(self.position(self.spanner)[0], D("0"))

    def test_a_reversed_stock_in_is_sent_the_way_the_goods_went(self):
        doc = self.stock_in((self.spanner, "10", "1.85"))
        self._as(self.admin, "owner-pass-99")
        res = self.client.post(f"/api/inventory/stock-ins/{doc.pk}/reverse/", {"note": "Keyed twice"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        line = res.data["lines"][0]
        self.assertEqual(res.data["total"], "-18.50")
        self.assertEqual((line["quantity"], line["line_total"]), ("-10.00", "-18.50"))
        # What was typed is sent as stored.
        self.assertEqual((line["packs"], line["pack_cost"]), ("10.00", "1.8500"))
        # The original is untouched.
        self.assertEqual(self.client.get(f"/api/inventory/stock-ins/{doc.pk}/").data["total"], "18.50")

    def test_a_reversed_write_off_comes_back_in(self):
        self.stock_in((self.spanner, "10", "1.85"))
        doc = self.adjust(R.DAMAGE, (self.spanner, "2"))
        self._as(self.admin, "owner-pass-99")
        res = self.client.post(f"/api/inventory/adjustments/{doc.pk}/reverse/", {"note": "Not damaged"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual((res.data["direction"], res.data["total"]), ("IN", "3.70"))
        self.assertEqual((res.data["lines"][0]["quantity"], res.data["lines"][0]["value"]), ("2.00", "3.70"))
        original = self.client.get(f"/api/inventory/adjustments/{doc.pk}/").data
        self.assertEqual((original["direction"], original["total"]), ("OUT", "-3.70"))

    def test_a_reversed_count_undoes_each_difference(self):
        self.stock_in((self.spanner, "10", "1.85"))
        self._as(self.admin, "owner-pass-99")
        res = self.client.post("/api/inventory/counts/", {"category": self.wrenches.pk}, format="json")
        url = f"/api/inventory/counts/{res.data['id']}/"
        ids = {l["product"]: l["id"] for l in res.data["lines"]}
        self.client.post(url + "record/", {"lines": [
            {"line": ids[self.spanner.pk], "counted_qty": "9"},
            {"line": ids[self.spanner_set.pk], "counted_qty": "0"},
        ]}, format="json")
        self.assertEqual(self.client.post(url + "post/").status_code, 200)

        res = self.client.post(url + "reverse/", {"note": "Wrong shelf"}, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        lines = {l["product"]: l for l in res.data["lines"]}
        short = lines[self.spanner.pk]
        # Counted and expected swap, so counted − expected is still the difference.
        self.assertEqual(
            (short["counted_qty"], short["expected_qty"], short["difference"], short["value"]),
            ("10.00", "9.00", "1.00", "1.85"),
        )
        self.assertEqual((lines[self.spanner_set.pk]["difference"], lines[self.spanner_set.pk]["value"]), ("0.00", "0.00"))
        self.assertEqual((res.data["differences"], res.data["total"]), (1, "1.85"))
        original = self.client.get(url).data
        self.assertEqual(original["total"], "-1.85")

    def test_a_product_with_stock_history_keeps_tracking_stock(self):
        self.stock_in((self.spanner, "1", "1"))
        self._as(self.admin, "owner-pass-99")
        res = self.client.patch(
            f"/api/catalogue/products/{self.spanner.pk}/", {"track_stock": False}, format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("track_stock", res.data)


class ImportTests(InventoryTestCase):
    CSV = (
        "Product code,Qty,Unit cost\n"
        "FX-0302,48,1.10\n"
        "TL-9999,2,12.00\n"
        "tl-0241,4,\n"
        "SV-0001,1,1.50\n"
        ",,\n"
        "TL-0240,10,1.85\n"
    )

    def upload(self, doc, content, name="lines.csv", **fields):
        self._as(self.admin, "owner-pass-99")
        kind = "stock-ins" if isinstance(doc, StockIn) else "adjustments"
        data = {"file": SimpleUploadedFile(name, content if isinstance(content, bytes) else content.encode())}
        data.update(fields)
        return self.client.post(f"/api/inventory/{kind}/{doc.pk}/import/", data, format="multipart")

    def test_the_check_reports_every_row_and_adds_nothing(self):
        doc = self.stock_in(post=False)
        res = self.upload(doc, self.CSV)
        self.assertEqual(res.status_code, 200, res.data)
        results = [(r["row"], r["result"]) for r in res.data["rows"]]
        self.assertEqual(results, [
            (2, "Matched"), (3, "No such code"), (4, "Cost missing"),
            (5, "Does not track stock"), (7, "Matched"),
        ])
        self.assertEqual((res.data["ready"], res.data["to_fix"], res.data["imported"]), (2, 3, 0))
        self.assertFalse(doc.lines.exists())

    def test_commit_adds_only_rows_that_passed_and_replace_clears_first(self):
        doc = self.stock_in((self.spanner_set, "1", "18.40"), post=False)
        res = self.upload(doc, self.CSV, commit="true")
        self.assertEqual(res.data["imported"], 2)
        self.assertEqual(doc.lines.count(), 3)
        res = self.upload(doc, self.CSV, commit="true", replace="true")
        self.assertEqual(
            sorted(doc.lines.values_list("product__code", flat=True)), ["FX-0302", "TL-0240"]
        )

    def test_excel_with_pack_columns(self):
        from openpyxl import Workbook

        book = Workbook()
        sheet = book.active
        sheet.append(["code", "quantity", "unit cost", "pack size", "pack unit"])
        sheet.append(["FX-0302", 2, 26.4, 24, "Carton"])
        buffer = io.BytesIO()
        book.save(buffer)

        doc = self.stock_in(post=False)
        res = self.upload(doc, buffer.getvalue(), name="lines.xlsx", commit="true")
        self.assertEqual(res.status_code, 200, res.data)
        line = doc.lines.get()
        self.assertEqual((line.packs, line.pack_size, line.quantity, line.unit_cost), (D("2"), D("24"), D("48"), D("1.1")))
        self.assertEqual(line.pack_unit.code, "CTN")

    def test_rows_without_pack_columns_take_the_supplier_link(self):
        ProductSupplier.objects.create(
            product=self.screw, supplier=self.supplier,
            pack_unit=Unit.objects.get(code="CTN"), pack_size=D("24"),
        )
        doc = self.stock_in(post=False)
        res = self.upload(doc, "code,quantity,unit_cost\nFX-0302,2,26.40\n", commit="true")
        self.assertEqual(res.data["imported"], 1)
        line = doc.lines.get()
        self.assertEqual((line.pack_unit.code, line.pack_size, line.quantity), ("CTN", D("24"), D("48")))

    def test_opening_balance_import_flags_products_the_ledger_has_moved(self):
        self.stock_in((self.spanner, "1", "1"))
        doc = self.adjust(R.OPENING_BALANCE, post=False)
        res = self.upload(doc, "code,quantity,unit_cost\nTL-0240,10,1.85\nTL-0241,4,18.40\nTL-0241,1,18.40\n")
        self.assertEqual(
            [r["result"] for r in res.data["rows"]],
            ["Already has stock movements", "Matched", "Listed twice"],
        )

    def test_a_posted_document_takes_no_import(self):
        doc = self.stock_in((self.spanner, "1", "1"))
        res = self.upload(doc, self.CSV, commit="true")
        self.assertEqual(res.status_code, 400)

    def test_an_unknown_file_type_is_refused(self):
        doc = self.stock_in(post=False)
        res = self.upload(doc, b"nonsense", name="lines.pdf")
        self.assertEqual(res.status_code, 400)


class SeedStockDemoTests(TestCase):
    """The sample history goes through the real services and agrees with the ledger."""

    def setUp(self):
        call_command("seed", stdout=StringIO())
        call_command("seed_demo", stdout=StringIO())
        User.objects.create_user(
            username="owner", password="owner-pass-99", full_name="Bopha Ly", role=Role.ADMIN,
        )
        self.since = timezone.localdate() - timedelta(days=45)

    def load(self, *extra):
        call_command(
            "seed_stock_demo", "--user", "owner", "--since", self.since.isoformat(), *extra,
            stdout=StringIO(),
        )

    def test_a_history_that_agrees_with_the_ledger(self):
        self.load()
        ins = StockIn.objects.filter(status=DocStatus.POSTED)
        self.assertGreaterEqual(ins.filter(reverses=None).count(), 10)
        self.assertEqual(ins.exclude(reverses=None).count(), 1)
        self.assertEqual(StockIn.objects.filter(status=DocStatus.DRAFT).count(), 1)
        reasons = set(
            Adjustment.objects.filter(status=DocStatus.POSTED).values_list("reason", flat=True)
        )
        self.assertLessEqual(
            {R.OPENING_BALANCE, R.DAMAGE, R.SHOP_USE, R.RETURN_TO_SUPPLIER, R.LOSS,
             R.SUPPLIER_REPLACEMENT},
            reasons,
        )
        self.assertEqual(Adjustment.objects.filter(status=DocStatus.DRAFT).count(), 1)
        self.assertGreaterEqual(StockCount.objects.filter(status=DocStatus.POSTED).count(), 1)
        self.assertEqual(StockCount.objects.filter(status=DocStatus.DRAFT).count(), 1)

        stocked = Product.objects.filter(track_stock=True, is_active=True)
        self.assertFalse(stocked.filter(movements__isnull=True).exists())
        self.assertFalse(Product.objects.filter(qty_on_hand__lt=0).exists())
        call_command("recompute_stock", "--check", stdout=StringIO())  # raises on any drift

        # Each document is stamped on its own date, and numbered in date order.
        for model in (StockIn, Adjustment, StockCount):
            docs = list(model.objects.order_by("number"))
            for doc in docs:
                self.assertEqual(timezone.localdate(doc.created_at), doc.doc_date, doc.number)
            dates = [doc.doc_date for doc in docs]
            self.assertEqual(dates, sorted(dates), model.__name__)
        # The ledger runs in date order across documents, so a month-end count
        # read the stock as it stood that day.
        moved = list(StockMovement.objects.order_by("id").values_list("movement_date", flat=True))
        self.assertEqual(moved, sorted(moved))

    def test_it_never_loads_over_stock_records(self):
        self.load()
        with self.assertRaises(CommandError):
            self.load()

    def test_reset_loads_the_same_again_but_not_once_there_are_sales(self):
        self.load()
        first = list(StockIn.objects.order_by("id").values_list("number", "doc_date", "supplier_ref"))
        self.load("--reset")
        again = list(StockIn.objects.order_by("id").values_list("number", "doc_date", "supplier_ref"))
        self.assertEqual(again, first)

        from partners.models import Customer
        from sales.models import Invoice, InvoiceStatus

        Invoice.objects.create(
            customer=Customer.walk_in(), status=InvoiceStatus.COMPLETED, number="INV-TEST-001",
        )
        with self.assertRaises(CommandError):
            self.load("--reset")
