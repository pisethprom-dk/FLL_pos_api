# v1.0.3 — the catalogue rules that matter
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from catalogue.models import Brand, Category, Product, Unit
from users.models import Role, User


def make_product(**kw):
    defaults = {
        "code": "TL-0101",
        "name": "Impact drill 13mm 710W",
        "retail_price": Decimal("78.00"),
        "wholesale_price": Decimal("69.00"),
    }
    defaults.update(kw)
    return Product.objects.create(**defaults)


class CategoryDepthTests(TestCase):
    def setUp(self):
        self.top = Category.objects.create(code="CAT-01", name="Power tools")
        self.sub = Category.objects.create(
            code="CAT-0101", name="Drills", parent=self.top
        )

    def test_a_sub_category_is_allowed(self):
        self.assertEqual(str(self.sub), "Power tools → Drills")
        self.assertFalse(self.sub.is_top_level)

    def test_a_third_level_is_refused(self):
        third = Category(code="CAT-010101", name="Hammer drills", parent=self.sub)
        with self.assertRaises(ValidationError):
            third.full_clean()

    def test_a_category_cannot_parent_itself(self):
        self.top.parent = self.top
        with self.assertRaises(ValidationError):
            self.top.full_clean()


class ProductTests(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(code="CAT-01", name="Power tools")
        self.unit = Unit.objects.create(code="PCS", name="Piece")
        self.brand = Brand.objects.create(name="Bosch", country="Germany")

    def test_blank_barcode_is_stored_as_null_so_several_are_allowed(self):
        a = make_product(code="A1", category=self.cat, unit=self.unit, barcode="")
        b = make_product(code="A2", category=self.cat, unit=self.unit, barcode="")
        self.assertIsNone(a.barcode)
        self.assertIsNone(b.barcode)

    def test_duplicate_real_barcodes_are_refused(self):
        from django.db.utils import IntegrityError

        make_product(code="A1", category=self.cat, unit=self.unit, barcode="8801234500117")
        with self.assertRaises(IntegrityError):
            make_product(
                code="A2", category=self.cat, unit=self.unit, barcode="8801234500117"
            )

    def test_shelf_location_is_uppercased(self):
        p = make_product(
            category=self.cat, unit=self.unit, shelf_location="a3-2"
        )
        self.assertEqual(p.shelf_location, "A3-2")

    def test_short_name_falls_back_to_the_name(self):
        p = make_product(category=self.cat, unit=self.unit)
        self.assertEqual(p.short_name, "Impact drill 13mm 710W")

    def test_price_for_tier(self):
        p = make_product(category=self.cat, unit=self.unit)
        self.assertEqual(p.price_for("WHOLESALE"), Decimal("69.00"))
        self.assertEqual(p.price_for("RETAIL"), Decimal("78.00"))

    def test_quantity_and_cost_start_at_zero_and_are_not_editable(self):
        p = make_product(category=self.cat, unit=self.unit)
        self.assertEqual(p.qty_on_hand, Decimal("0.00"))
        self.assertEqual(p.avg_cost, Decimal("0.0000"))
        self.assertFalse(Product._meta.get_field("qty_on_hand").editable)
        self.assertFalse(Product._meta.get_field("avg_cost").editable)

    def test_reorder_and_stock_flags(self):
        p = make_product(
            category=self.cat, unit=self.unit, reorder_level=Decimal("6")
        )
        self.assertTrue(p.needs_reorder)
        self.assertTrue(p.is_out_of_stock)

        service = make_product(
            code="SV-0001", name="Key cutting", category=self.cat,
            unit=self.unit, track_stock=False,
        )
        self.assertFalse(service.needs_reorder)
        self.assertFalse(service.is_out_of_stock)
        self.assertEqual(service.stock_value, Decimal("0"))

    def test_negative_price_is_refused_by_the_database(self):
        from django.db.utils import IntegrityError

        with self.assertRaises(IntegrityError):
            make_product(
                category=self.cat, unit=self.unit, retail_price=Decimal("-1.00")
            )


class CatalogueApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="owner", password="owner-pass-99", full_name="Bopha Ly",
            role=Role.ADMIN,
        )
        self.seller = User.objects.create_user(
            username="dara", password="counter-pass-99", full_name="Dara Meas",
            role=Role.SELLER,
        )
        self.cat = Category.objects.create(code="CAT-01", name="Power tools")
        self.sub = Category.objects.create(
            code="CAT-0101", name="Drills", parent=self.cat
        )
        self.unit = Unit.objects.create(code="PCS", name="Piece")
        self.brand = Brand.objects.create(name="Bosch")
        self.product = make_product(
            category=self.sub, unit=self.unit, brand=self.brand,
            shelf_location="A1-1",
        )

    def _as(self, user, password):
        res = self.client.post(
            reverse("auth-login"),
            {"username": user.username, "password": password},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def test_seller_reads_products_but_cannot_change_them(self):
        self._as(self.seller, "counter-pass-99")
        self.assertEqual(self.client.get("/api/catalogue/products/").status_code, 200)
        res = self.client.patch(
            f"/api/catalogue/products/{self.product.pk}/",
            {"retail_price": "1.00"},
            format="json",
        )
        self.assertEqual(res.status_code, 403)

    def test_a_seller_gets_no_cost_or_stock_value(self):
        Product.objects.filter(pk=self.product.pk).update(
            avg_cost=Decimal("52.4000"), qty_on_hand=Decimal("3.00")
        )
        self._as(self.seller, "counter-pass-99")
        row = self.client.get(f"/api/catalogue/products/{self.product.pk}/").data
        # Null, never left out, so the Angular client's type stays true.
        self.assertIn("avg_cost", row)
        self.assertIsNone(row["avg_cost"])
        self.assertIsNone(row["stock_value"])
        listed = self.client.get("/api/catalogue/products/").data["results"][0]
        self.assertIsNone(listed["avg_cost"])

    def test_an_admin_sees_cost_and_stock_value(self):
        Product.objects.filter(pk=self.product.pk).update(
            avg_cost=Decimal("52.4000"), qty_on_hand=Decimal("3.00")
        )
        self._as(self.admin, "owner-pass-99")
        row = self.client.get(f"/api/catalogue/products/{self.product.pk}/").data
        self.assertEqual(row["avg_cost"], "52.4000")
        self.assertEqual(row["stock_value"], "157.20")

    def test_quantity_sent_to_the_api_is_ignored(self):
        self._as(self.admin, "owner-pass-99")
        res = self.client.patch(
            f"/api/catalogue/products/{self.product.pk}/",
            {"qty_on_hand": "999", "avg_cost": "1.0000"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.qty_on_hand, Decimal("0.00"))
        self.assertEqual(self.product.avg_cost, Decimal("0.0000"))

    def test_filtering_by_a_top_level_category_includes_its_children(self):
        self._as(self.seller, "counter-pass-99")
        res = self.client.get(f"/api/catalogue/products/?category={self.cat.pk}")
        self.assertEqual(res.data["count"], 1)

    def test_search_matches_code_name_and_model(self):
        self._as(self.seller, "counter-pass-99")
        for term in ["TL-0101", "Impact", "drill"]:
            res = self.client.get(f"/api/catalogue/products/?search={term}")
            self.assertEqual(res.data["count"], 1, term)

    def test_api_refuses_a_third_category_level(self):
        self._as(self.admin, "owner-pass-99")
        res = self.client.post(
            "/api/catalogue/categories/",
            {"code": "CAT-010101", "name": "Hammer drills", "parent": self.sub.pk},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("parent", res.data)

    def test_lookup_returns_a_small_payload(self):
        self._as(self.seller, "counter-pass-99")
        res = self.client.get("/api/catalogue/products/lookup/")
        self.assertEqual(res.status_code, 200)
        row = res.data[0]
        self.assertIn("wholesale_price", row)
        self.assertIn("shelf_location", row)
        # A warranty claim picked from the catalogue takes its duration.
        self.assertIn("warranty_months", row)
        self.assertNotIn("description", row)


class SeedDemoTests(TestCase):
    def setUp(self):
        call_command("seed_demo", stdout=StringIO())

    def test_loads_every_row_and_is_safe_to_rerun(self):
        from catalogue.demo_data import BRANDS, CATEGORIES, PRODUCTS, UNITS

        expected = sum(len(rows) for rows in PRODUCTS.values())
        call_command("seed_demo", stdout=StringIO())
        self.assertEqual(Product.objects.count(), expected)
        self.assertEqual(Category.objects.count(), len(CATEGORIES))
        self.assertEqual(Brand.objects.count(), len(BRANDS))
        self.assertEqual(Unit.objects.count(), len(UNITS))

    def test_an_edit_survives_a_rerun(self):
        Product.objects.filter(code="TL-0101").update(retail_price=Decimal("80.00"))
        call_command("seed_demo", stdout=StringIO())
        self.assertEqual(Product.objects.get(code="TL-0101").retail_price, Decimal("80.00"))

    def test_mockup_products_match_the_design(self):
        drill = Product.objects.get(code="TL-0101")
        self.assertEqual(str(drill.category), "Power tools → Drills")
        self.assertEqual((drill.retail_price, drill.wholesale_price), (Decimal("78.00"), Decimal("69.00")))
        self.assertEqual(Product.objects.get(code="FX-0302").unit.name, "Box of 100")
        self.assertTrue(Product.objects.get(code="TL-0150").is_price_fixed)

    def test_stock_is_left_to_the_ledger(self):
        self.assertFalse(Product.objects.exclude(qty_on_hand=0).exists())
        self.assertFalse(Product.objects.exclude(avg_cost=0).exists())

    def test_services_have_no_stock_and_no_barcode(self):
        services = Product.objects.filter(category__code="CAT-05")
        self.assertTrue(services.exists())
        self.assertFalse(services.filter(track_stock=True).exists())
        self.assertFalse(services.exclude(barcode=None).exists())

    def test_barcodes_are_valid_in_store_ean13(self):
        for code in Product.objects.exclude(barcode=None).values_list("barcode", flat=True):
            self.assertTrue(code.startswith("200"), code)
            digits = [int(d) for d in code]
            weighted = sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits[:12]))
            self.assertEqual((10 - weighted % 10) % 10, digits[12], code)

    def test_seed_units_get_their_khmer_name(self):
        Unit.objects.filter(code="PCS").update(name_kh="")
        call_command("seed_demo", stdout=StringIO())
        self.assertEqual(Unit.objects.get(code="PCS").name_kh, "ដុំ")

    def test_runs_after_seed_without_clashing(self):
        call_command("seed", stdout=StringIO())
        call_command("seed_demo", stdout=StringIO())
        self.assertEqual(Unit.objects.filter(code="PCS").count(), 1)
