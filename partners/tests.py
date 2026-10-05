# v1.0.0 — the partner rules that matter
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from catalogue.models import Category, Product, Unit
from core.exceptions import DomainError
from partners.models import Customer, PriceTier, ProductSupplier, Supplier, SupplierType
from users.models import Role, User


def seed():
    call_command("seed", stdout=StringIO())


def make_supplier(**kw):
    defaults = {"name": "Total Tools (Cambodia)", "supplier_type": SupplierType.DISTRIBUTOR}
    defaults.update(kw)
    return Supplier.objects.create(**defaults)


class WalkInTests(TestCase):
    def setUp(self):
        seed()
        self.walk_in = Customer.walk_in()

    def test_seed_makes_exactly_one_and_is_safe_to_rerun(self):
        seed()
        self.assertEqual(Customer.objects.filter(is_system=True).count(), 1)
        self.assertEqual(self.walk_in.code, "CUS-000000")
        self.assertEqual(self.walk_in.price_tier, PriceTier.RETAIL)
        self.assertFalse(self.walk_in.allow_credit)

    def test_a_second_system_customer_is_refused_by_the_database(self):
        with self.assertRaises(IntegrityError):
            Customer.objects.create(name="Another walk-in", is_system=True)

    def test_it_can_never_be_given_credit(self):
        self.walk_in.allow_credit = True
        self.walk_in.credit_limit = Decimal("100.00")
        with self.assertRaises(ValidationError):
            self.walk_in.full_clean()
        with self.assertRaises(DomainError):
            self.walk_in.save()

    def test_it_always_pays_retail(self):
        self.walk_in.price_tier = PriceTier.WHOLESALE
        with self.assertRaises(DomainError):
            self.walk_in.save()

    def test_it_cannot_be_renamed_or_renumbered(self):
        self.walk_in.name = "Cash customer"
        with self.assertRaises(DomainError):
            self.walk_in.save()
        self.walk_in.refresh_from_db()
        self.walk_in.code = "CUS-999999"
        with self.assertRaises(DomainError):
            self.walk_in.save()

    def test_it_cannot_be_deactivated_or_deleted(self):
        self.walk_in.is_active = False
        with self.assertRaises(DomainError):
            self.walk_in.save()
        self.walk_in.refresh_from_db()
        with self.assertRaises(DomainError):
            self.walk_in.delete()
        self.assertTrue(Customer.objects.filter(is_system=True).exists())

    def test_the_database_holds_the_line_even_past_save(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Customer.objects.filter(is_system=True).update(allow_credit=True, credit_limit=1)


class CustomerCreditTests(TestCase):
    def setUp(self):
        seed()

    def test_credit_off_clears_limit_terms_and_hold(self):
        c = Customer.objects.create(
            name="Sok Shop", allow_credit=True,
            credit_limit=Decimal("1500.00"), payment_terms_days=30,
        )
        c.allow_credit = False
        c.credit_hold = True
        c.save()
        c.refresh_from_db()
        self.assertEqual(c.credit_limit, Decimal("0.00"))
        self.assertEqual(c.payment_terms_days, 0)
        self.assertFalse(c.credit_hold)

    def test_credit_on_needs_a_limit_above_zero(self):
        c = Customer(name="Sok Shop", allow_credit=True, credit_limit=Decimal("0"))
        with self.assertRaises(ValidationError) as ctx:
            c.full_clean()
        self.assertIn("credit_limit", ctx.exception.message_dict)

    def test_price_and_credit_are_independent(self):
        # Bopha buys at wholesale but pays cash.
        c = Customer.objects.create(name="Bopha (Street 315)", price_tier=PriceTier.WHOLESALE)
        self.assertEqual(c.price_tier, PriceTier.WHOLESALE)
        self.assertEqual(c.credit_status, "NO")

    def test_credit_status(self):
        c = Customer.objects.create(
            name="Dara Grocery", allow_credit=True, credit_limit=Decimal("1200"),
        )
        self.assertEqual(c.credit_status, "YES")
        c.credit_hold = True
        c.save()
        self.assertEqual(c.credit_status, "HOLD")
        self.assertEqual(c.credit_limit, Decimal("1200"))


class CodeNumberingTests(TestCase):
    def setUp(self):
        seed()

    def test_a_blank_code_is_numbered_six_digits(self):
        self.assertEqual(Customer.objects.create(name="Sok Shop").code, "CUS-000001")
        self.assertEqual(Customer.objects.create(name="Dara Grocery").code, "CUS-000002")
        self.assertEqual(make_supplier().code, "SUP-000001")

    def test_a_code_typed_by_hand_is_kept_and_skipped_later(self):
        Customer.objects.create(code="CUS-000001", name="Typed by hand")
        self.assertEqual(Customer.objects.create(name="Numbered").code, "CUS-000002")

    def test_a_typed_code_is_trimmed(self):
        self.assertEqual(Customer.objects.create(code="  VIP-01 ", name="x").code, "VIP-01")

    def test_short_name_falls_back_to_the_name(self):
        self.assertEqual(Customer.objects.create(name="Kim Long Trading").short_name, "Kim Long Trading")


class ProductSupplierTests(TestCase):
    def setUp(self):
        seed()
        cat = Category.objects.create(code="CAT-01", name="Power tools")
        self.product = Product.objects.create(
            code="TL-0101", name="Impact drill 13mm 710W", category=cat,
            unit=Unit.objects.get(code="PCS"),
        )
        self.total = make_supplier()
        self.lim = make_supplier(name="Lim Heng Import Export", supplier_type=SupplierType.IMPORTER)

    def test_one_link_per_product_and_supplier(self):
        ProductSupplier.objects.create(product=self.product, supplier=self.total)
        with self.assertRaises(IntegrityError):
            ProductSupplier.objects.create(product=self.product, supplier=self.total)

    def test_marking_a_supplier_preferred_unmarks_the_last(self):
        a = ProductSupplier.objects.create(product=self.product, supplier=self.total, is_preferred=True)
        ProductSupplier.objects.create(product=self.product, supplier=self.lim, is_preferred=True)
        a.refresh_from_db()
        self.assertFalse(a.is_preferred)
        self.assertEqual(
            ProductSupplier.objects.filter(product=self.product, is_preferred=True).count(), 1
        )

    def test_pack_size_must_be_positive(self):
        with self.assertRaises(IntegrityError):
            ProductSupplier.objects.create(product=self.product, supplier=self.total, pack_size=0)

    def test_an_inactive_supplier_cannot_be_linked(self):
        self.lim.is_active = False
        self.lim.save()
        link = ProductSupplier(product=self.product, supplier=self.lim)
        with self.assertRaises(ValidationError):
            link.full_clean()

    def test_an_existing_link_survives_its_supplier_being_retired(self):
        link = ProductSupplier.objects.create(product=self.product, supplier=self.lim)
        self.lim.is_active = False
        self.lim.save()
        link.notes = "Stopped carrying Bosch"
        link.full_clean()


class PartnersApiTests(TestCase):
    def setUp(self):
        seed()
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="owner", password="owner-pass-99", full_name="Bopha Ly",
            role=Role.ADMIN,
        )
        self.seller = User.objects.create_user(
            username="dara", password="counter-pass-99", full_name="Dara Meas",
            role=Role.SELLER,
        )
        self.sok = Customer.objects.create(
            name="Sok Shop", price_tier=PriceTier.WHOLESALE,
            allow_credit=True, credit_limit=Decimal("1500"), payment_terms_days=30,
        )
        self.dara = Customer.objects.create(
            name="Dara Grocery", price_tier=PriceTier.WHOLESALE,
            allow_credit=True, credit_limit=Decimal("1200"), credit_hold=True,
        )
        self.walk_in = Customer.walk_in()

    def _as(self, user, password):
        res = self.client.post(
            reverse("auth-login"),
            {"username": user.username, "password": password},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def test_seller_reads_customers_but_cannot_change_them(self):
        self._as(self.seller, "counter-pass-99")
        self.assertEqual(self.client.get("/api/partners/customers/").status_code, 200)
        res = self.client.post("/api/partners/customers/", {"name": "New"}, format="json")
        self.assertEqual(res.status_code, 403)
        res = self.client.post(
            "/api/partners/suppliers/",
            {"name": "New", "supplier_type": "IMPORTER"}, format="json",
        )
        self.assertEqual(res.status_code, 403)

    def test_admin_creates_a_customer_and_the_code_is_numbered(self):
        self._as(self.admin, "owner-pass-99")
        res = self.client.post(
            "/api/partners/customers/",
            {"code": "", "name": "Kim Long Trading", "price_tier": "WHOLESALE"},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["code"], "CUS-000003")
        created = Customer.objects.get(pk=res.data["id"])
        self.assertEqual(created.created_by, self.admin)

    def test_nothing_can_be_deleted_through_the_api(self):
        self._as(self.admin, "owner-pass-99")
        self.assertEqual(self.client.delete(f"/api/partners/customers/{self.sok.pk}/").status_code, 405)
        supplier = make_supplier()
        self.assertEqual(self.client.delete(f"/api/partners/suppliers/{supplier.pk}/").status_code, 405)

    def test_the_api_cannot_make_a_system_customer(self):
        self._as(self.admin, "owner-pass-99")
        res = self.client.post(
            "/api/partners/customers/", {"name": "Sneaky", "is_system": True}, format="json"
        )
        self.assertEqual(res.status_code, 201)
        self.assertFalse(Customer.objects.get(pk=res.data["id"]).is_system)

    def test_the_api_refuses_walk_in_changes(self):
        self._as(self.admin, "owner-pass-99")
        url = f"/api/partners/customers/{self.walk_in.pk}/"
        for body, field in [
            ({"allow_credit": True, "credit_limit": "100"}, "allow_credit"),
            ({"name": "Cash customer"}, "name"),
            ({"is_active": False}, "is_active"),
            ({"price_tier": "WHOLESALE"}, "price_tier"),
        ]:
            res = self.client.patch(url, body, format="json")
            self.assertEqual(res.status_code, 400, body)
            self.assertIn(field, res.data)

    def test_the_api_refuses_credit_without_a_limit(self):
        self._as(self.admin, "owner-pass-99")
        res = self.client.post(
            "/api/partners/customers/",
            {"name": "No limit", "allow_credit": True}, format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("credit_limit", res.data)

    def test_customer_filters(self):
        self._as(self.seller, "counter-pass-99")
        res = self.client.get("/api/partners/customers/?credit=hold")
        self.assertEqual([r["code"] for r in res.data["results"]], [self.dara.code])
        res = self.client.get("/api/partners/customers/?credit=yes")
        self.assertEqual(res.data["count"], 2)
        res = self.client.get("/api/partners/customers/?credit=no")
        self.assertEqual([r["code"] for r in res.data["results"]], ["CUS-000000"])
        res = self.client.get("/api/partners/customers/?price_tier=retail")
        self.assertEqual(res.data["count"], 1)
        res = self.client.get("/api/partners/customers/?search=grocery")
        self.assertEqual(res.data["count"], 1)

    def test_lookup_puts_walk_in_first_and_skips_inactive(self):
        Customer.objects.create(name="Chhay Mini Mart", is_active=False, display_order=-5)
        self._as(self.seller, "counter-pass-99")
        res = self.client.get("/api/partners/customers/lookup/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data[0]["is_system"])
        self.assertNotIn("Chhay Mini Mart", [r["name"] for r in res.data])
        self.assertIn("credit_status", res.data[0])
        self.assertNotIn("notes", res.data[0])

    def test_supplier_type_filter(self):
        make_supplier()
        make_supplier(name="Makita service centre", supplier_type=SupplierType.SERVICE_CENTRE)
        self._as(self.seller, "counter-pass-99")
        res = self.client.get("/api/partners/suppliers/?supplier_type=service_centre")
        self.assertEqual([r["name"] for r in res.data["results"]], ["Makita service centre"])

    def test_product_supplier_links_through_the_api(self):
        cat = Category.objects.create(code="CAT-01", name="Power tools")
        product = Product.objects.create(
            code="TL-0101", name="Impact drill", category=cat, unit=Unit.objects.get(code="PCS"),
        )
        total = make_supplier()
        lim = make_supplier(name="Lim Heng", supplier_type=SupplierType.IMPORTER)

        self._as(self.admin, "owner-pass-99")
        res = self.client.post(
            "/api/partners/product-suppliers/",
            {"product": product.pk, "supplier": total.pk, "is_preferred": True,
             "supplier_sku": "TT-GSB550", "pack_size": "1"},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        first = res.data["id"]

        # A second supplier for the same product, also marked preferred.
        res = self.client.post(
            "/api/partners/product-suppliers/",
            {"product": product.pk, "supplier": lim.pk, "is_preferred": True},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertFalse(ProductSupplier.objects.get(pk=first).is_preferred)

        res = self.client.post(
            "/api/partners/product-suppliers/",
            {"product": product.pk, "supplier": lim.pk}, format="json",
        )
        self.assertEqual(res.status_code, 400)

        res = self.client.get(f"/api/partners/product-suppliers/?product={product.pk}&preferred=true")
        self.assertEqual([r["supplier_name"] for r in res.data["results"]], ["Lim Heng"])

        self.assertEqual(self.client.delete(f"/api/partners/product-suppliers/{first}/").status_code, 204)

    def test_seller_cannot_change_product_supplier_links(self):
        self._as(self.seller, "counter-pass-99")
        self.assertEqual(self.client.get("/api/partners/product-suppliers/").status_code, 200)
        res = self.client.post("/api/partners/product-suppliers/", {}, format="json")
        self.assertEqual(res.status_code, 403)
