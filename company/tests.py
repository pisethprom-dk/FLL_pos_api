# v1.0.3 — the company rules that matter
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from company.currency import round_khr, split_change, usd_to_khr
from company.models import CompanyProfile, DocumentCounter, DocumentType, ExchangeRate
from company.services import next_document_number, rate_on, NoExchangeRateError
from users.models import Role, User


class RielRoundingTests(TestCase):
    def test_rounds_to_the_nearest_hundred(self):
        self.assertEqual(round_khr(Decimal("2255")), Decimal("2300"))
        self.assertEqual(round_khr(Decimal("2240")), Decimal("2200"))
        self.assertEqual(round_khr(Decimal("2250")), Decimal("2300"))
        self.assertEqual(round_khr(Decimal("49")), Decimal("0"))

    def test_conversion_rounds_after_multiplying(self):
        # $320.00 at 4,100 is exact
        self.assertEqual(usd_to_khr(Decimal("320.00"), Decimal("4100")), Decimal("1312000"))
        # $361.45 at 4,100 is 1,481,945 -> 1,481,900
        self.assertEqual(usd_to_khr(Decimal("361.45"), Decimal("4100")), Decimal("1481900"))

    def test_change_is_split_into_dollars_and_riel(self):
        # $38.55 change at 4,100 -> $38 plus 2,255 riel rounded to 2,300
        dollars, riel = split_change(Decimal("38.55"), Decimal("4100"))
        self.assertEqual(dollars, Decimal("38"))
        self.assertEqual(riel, Decimal("2300"))

    def test_whole_dollar_change_needs_no_riel(self):
        dollars, riel = split_change(Decimal("12.00"), Decimal("4100"))
        self.assertEqual(dollars, Decimal("12"))
        self.assertEqual(riel, Decimal("0"))


class ExchangeRateTests(TestCase):
    def setUp(self):
        for d, r in [
            (date(2026, 1, 1), "4000"),
            (date(2026, 6, 1), "4050"),
            (date(2026, 7, 1), "4100"),
            (date(2026, 8, 1), "4080"),
            (date(2026, 9, 1), "4100"),
        ]:
            ExchangeRate.objects.create(effective_date=d, rate=Decimal(r))

    def test_rate_on_a_date_is_the_one_in_force_then(self):
        # the rule that keeps an old invoice reprinting as issued
        self.assertEqual(rate_on(date(2026, 6, 12)).rate, Decimal("4050.000000"))
        self.assertEqual(rate_on(date(2026, 8, 15)).rate, Decimal("4080.000000"))
        self.assertEqual(rate_on(date(2026, 9, 30)).rate, Decimal("4100.000000"))

    def test_rate_on_the_effective_date_itself_counts(self):
        self.assertEqual(rate_on(date(2026, 8, 1)).rate, Decimal("4080.000000"))

    def test_a_date_before_any_rate_raises(self):
        with self.assertRaises(NoExchangeRateError):
            rate_on(date(2025, 12, 31))

    def test_two_rates_cannot_share_a_date(self):
        from django.db.utils import IntegrityError

        with self.assertRaises(IntegrityError):
            ExchangeRate.objects.create(
                effective_date=date(2026, 9, 1), rate=Decimal("4200")
            )

    def test_a_zero_rate_is_refused(self):
        from django.db.utils import IntegrityError

        with self.assertRaises(IntegrityError):
            ExchangeRate.objects.create(
                effective_date=date(2026, 10, 1), rate=Decimal("0")
            )


def on_day(day):
    """Run as if the shop's date were `day`."""
    return patch("django.utils.timezone.localdate", return_value=day)


class DocumentNumberTests(TestCase):
    OCT_5 = date(2026, 10, 5)

    def setUp(self):
        DocumentCounter.objects.create(
            doc_type=DocumentType.PAYMENT, prefix="PAY-", next_number=147
        )
        DocumentCounter.objects.create(doc_type=DocumentType.INVOICE, prefix="INV-")
        DocumentCounter.objects.create(doc_type=DocumentType.QUOTATION, prefix="QUO-")

    def test_most_documents_run_on_padded_to_six(self):
        self.assertEqual(next_document_number(DocumentType.PAYMENT), "PAY-000147")
        self.assertEqual(next_document_number(DocumentType.PAYMENT), "PAY-000148")
        counter = DocumentCounter.objects.get(doc_type=DocumentType.PAYMENT)
        self.assertEqual(counter.next_number, 149)

    def test_peek_does_not_advance(self):
        counter = DocumentCounter.objects.get(doc_type=DocumentType.PAYMENT)
        self.assertEqual(counter.peek(), "PAY-000147")
        self.assertEqual(counter.peek(), "PAY-000147")

    def test_invoices_and_quotations_are_numbered_by_day(self):
        with on_day(self.OCT_5):
            self.assertEqual(next_document_number(DocumentType.INVOICE), "INV-20261005001")
            self.assertEqual(next_document_number(DocumentType.INVOICE), "INV-20261005002")
            # Each type keeps its own count.
            self.assertEqual(next_document_number(DocumentType.QUOTATION), "QUO-20261005001")

    def test_the_count_starts_again_each_day(self):
        with on_day(self.OCT_5):
            next_document_number(DocumentType.INVOICE)
            next_document_number(DocumentType.INVOICE)
        with on_day(self.OCT_5 + timedelta(days=1)):
            self.assertEqual(next_document_number(DocumentType.INVOICE), "INV-20261006001")

    def test_peek_shows_001_on_a_new_day(self):
        with on_day(self.OCT_5):
            next_document_number(DocumentType.INVOICE)
        counter = DocumentCounter.objects.get(doc_type=DocumentType.INVOICE)
        self.assertEqual(counter.peek(self.OCT_5), "INV-20261005002")
        self.assertEqual(counter.peek(self.OCT_5 + timedelta(days=1)), "INV-20261006001")

    def test_past_999_in_a_day_grows_to_four_digits(self):
        DocumentCounter.objects.filter(doc_type=DocumentType.INVOICE).update(
            number_date=self.OCT_5, next_number=999
        )
        with on_day(self.OCT_5):
            self.assertEqual(next_document_number(DocumentType.INVOICE), "INV-20261005999")
            self.assertEqual(next_document_number(DocumentType.INVOICE), "INV-202610051000")

    def test_the_prefix_is_still_the_admins(self):
        DocumentCounter.objects.filter(doc_type=DocumentType.INVOICE).update(prefix="FLL-")
        with on_day(self.OCT_5):
            self.assertEqual(next_document_number(DocumentType.INVOICE), "FLL-20261005001")


class CompanyProfileTests(TestCase):
    def test_profile_is_a_singleton(self):
        a = CompanyProfile.get()
        a.name = "Sok Heng Mart"
        a.save()
        CompanyProfile.objects.create(name="Another shop")
        self.assertEqual(CompanyProfile.objects.count(), 1)
        self.assertEqual(CompanyProfile.objects.first().name, "Another shop")

    def test_profile_cannot_be_deleted(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            CompanyProfile.get().delete()


class CompanyBrandTests(TestCase):
    """The sign-in page shows the shop before anyone has signed in."""

    def setUp(self):
        self.client = APIClient()
        profile = CompanyProfile.get()
        profile.name = "FLL"
        profile.name_kh = "ហាងលក់ឧបករណ៍ជាង"
        profile.address = "Street 315, Toul Kork, Phnom Penh"
        profile.vat_tin = "K001-901234567"
        profile.phone = "012 345 678"
        profile.save()

    def test_anyone_may_read_the_brand(self):
        res = self.client.get("/api/company/brand/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["name"], "FLL")
        self.assertEqual(res.data["name_kh"], "ហាងលក់ឧបករណ៍ជាង")

    def test_it_shows_nothing_beyond_name_address_and_logo(self):
        res = self.client.get("/api/company/brand/")
        self.assertEqual(set(res.data), {"name", "name_kh", "address", "logo"})

    def test_a_stale_token_does_not_break_it(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer expired.or.forged")
        self.assertEqual(self.client.get("/api/company/brand/").status_code, 200)

    def test_it_cannot_be_written(self):
        res = self.client.patch("/api/company/brand/", {"name": "Hacked"}, format="json")
        self.assertEqual(res.status_code, 405)
        self.assertEqual(CompanyProfile.get().name, "FLL")


class CompanyApiTests(TestCase):
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
        ExchangeRate.objects.create(effective_date=date.today(), rate=Decimal("4100"))

    def _as(self, user, password):
        res = self.client.post(
            reverse("auth-login"),
            {"username": user.username, "password": password},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def test_seller_may_read_the_profile_but_not_change_it(self):
        self._as(self.seller, "counter-pass-99")
        self.assertEqual(self.client.get("/api/company/profile/").status_code, 200)
        res = self.client.patch(
            "/api/company/profile/", {"name": "Hacked"}, format="json"
        )
        self.assertEqual(res.status_code, 403)

    def test_admin_may_change_the_profile(self):
        self._as(self.admin, "owner-pass-99")
        res = self.client.patch(
            "/api/company/profile/", {"name": "Sok Heng Mart"}, format="json"
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(CompanyProfile.get().name, "Sok Heng Mart")

    def test_rate_endpoint_gives_the_till_what_it_needs(self):
        self._as(self.seller, "counter-pass-99")
        res = self.client.get("/api/company/rate/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["base_currency"], "USD")
        self.assertEqual(res.data["rounding_step"]["KHR"], "100")
        self.assertEqual(res.data["decimals"]["KHR"], 0)

    def test_seller_cannot_set_a_rate(self):
        self._as(self.seller, "counter-pass-99")
        res = self.client.post(
            "/api/company/exchange-rates/",
            {"effective_date": "2026-10-05", "rate": "4150"},
            format="json",
        )
        self.assertEqual(res.status_code, 403)

    def test_seller_cannot_see_numbering(self):
        self._as(self.seller, "counter-pass-99")
        self.assertEqual(self.client.get("/api/company/numbering/").status_code, 403)

    def test_a_rate_nobody_set_sends_set_by_as_null(self):
        # The rate from setUp, like seed's opening rate, has no created_by. The
        # field is still sent — as null — never left out.
        self._as(self.admin, "owner-pass-99")
        row = self.client.get("/api/company/exchange-rates/").data["results"][0]
        self.assertIn("set_by", row)
        self.assertIsNone(row["set_by"])

    def test_a_rate_set_by_a_user_names_them(self):
        self._as(self.admin, "owner-pass-99")
        later = date.today() + timedelta(days=30)
        res = self.client.post(
            "/api/company/exchange-rates/",
            {"effective_date": later.isoformat(), "rate": "4150"},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["set_by"], "Bopha Ly")
