# v1.0.0 — the warranty claim rules that matter
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from company.models import DocumentCounter
from users.models import Role, User
from warranty.models import ClaimStatus, WarrantyClaim

URL = "/api/warranty/claims/"


class ClaimTests(TestCase):
    def setUp(self):
        call_command("seed", stdout=StringIO())
        self.admin = User.objects.create_user(
            username="owner", password="owner-pass-99", full_name="Bopha Ly", role=Role.ADMIN,
        )
        self.seller = User.objects.create_user(
            username="dara", password="counter-pass-99", full_name="Dara Meas", role=Role.SELLER,
        )

    def _as(self, user, password):
        self.client = APIClient()
        res = self.client.post(
            reverse("auth-login"), {"username": user.username, "password": password}, format="json"
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def as_seller(self):
        self._as(self.seller, "counter-pass-99")

    def as_admin(self):
        self._as(self.admin, "owner-pass-99")

    def claim(self, **kw):
        fields = {"warranty_number": "WR-2026-0001", "product_name": "Impact drill 13mm 710W"}
        fields.update(kw)
        return WarrantyClaim.objects.create(**fields)

    def numbers(self, query=""):
        res = self.client.get(f"{URL}?{query}")
        self.assertEqual(res.status_code, 200)
        return [row["warranty_number"] for row in res.data["results"]]

    def test_a_seller_logs_a_claim_and_moves_it_along_in_any_order(self):
        self.as_seller()
        res = self.client.post(URL, {
            "warranty_number": " WR-2026-0149 ", "product_code": "TL-0101",
            "product_name": "Impact drill 13mm 710W", "warranty_months": 12,
            "expiry_date": "2027-09-30", "customer_name": "Sok Shop",
            "customer_phone": "012 330 441", "note": "Will not start.",
        }, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["warranty_number"], "WR-2026-0149")
        self.assertEqual(res.data["status"], ClaimStatus.RECEIVED)
        self.assertEqual(res.data["logged_by_name"], "Dara Meas")
        self.assertIsNone(res.data["changed_by_name"])
        self.assertFalse(res.data["out_of_warranty"])

        detail = f"{URL}{res.data['id']}/"
        for status in (ClaimStatus.SENT_FOR_REPAIR, ClaimStatus.CLOSED, ClaimStatus.READY):
            res = self.client.patch(detail, {"status": status}, format="json")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.data["status"], status)
        self.assertEqual(res.data["changed_by_name"], "Dara Meas")

    def test_only_a_card_number_and_a_product_name_are_needed(self):
        self.as_seller()
        res = self.client.post(URL, {"warranty_number": "  ", "product_name": " "}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(set(res.data), {"warranty_number", "product_name"})

        res = self.client.post(URL, {"warranty_number": "WR-1", "product_name": "Wood screw"}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(
            [res.data[f] for f in ("warranty_months", "expiry_date", "customer_name", "customer_phone")],
            [0, None, "", ""],
        )
        res = self.client.post(URL, {"warranty_number": "WR-2", "product_name": "Drill",
                                     "warranty_months": 121}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_one_card_can_be_claimed_twice(self):
        self.as_seller()
        body = {"warranty_number": "WR-2026-0098", "product_name": "Impact drill 13mm 710W"}
        self.assertEqual(self.client.post(URL, body, format="json").status_code, 201)
        self.assertEqual(self.client.post(URL, body, format="json").status_code, 201)

    def test_out_of_warranty_means_claimed_after_the_end_date(self):
        today = timezone.localdate()
        late = self.claim(warranty_number="LATE", expiry_date=today - timedelta(days=1))
        self.claim(warranty_number="LAST-DAY", expiry_date=today)
        self.claim(warranty_number="NO-DATE")
        # Claimed while still covered; the warranty has ended since.
        before = self.claim(warranty_number="IN-TIME", expiry_date=today - timedelta(days=5))
        WarrantyClaim.objects.filter(pk=before.pk).update(
            created_at=timezone.now() - timedelta(days=10)
        )
        self.assertTrue(late.out_of_warranty)
        self.as_seller()
        self.assertEqual(self.numbers("out_of_warranty=true"), ["LATE"])
        self.assertEqual(
            sorted(self.numbers("out_of_warranty=false")), ["IN-TIME", "LAST-DAY", "NO-DATE"]
        )
        res = self.client.get(f"{URL}{late.pk}/")
        self.assertTrue(res.data["out_of_warranty"])

    def test_filters_and_search(self):
        self.claim(warranty_number="A", status=ClaimStatus.RECEIVED, customer_phone="012 330 441")
        self.claim(warranty_number="B", status=ClaimStatus.READY, product_code="TL-0118")
        self.claim(warranty_number="C", status=ClaimStatus.CLOSED, customer_name="Chipmong")
        self.claim(warranty_number="D", status=ClaimStatus.REJECTED)
        self.as_seller()
        self.assertEqual(self.numbers(), ["D", "C", "B", "A"])
        self.assertEqual(self.numbers("open=true"), ["B", "A"])
        self.assertEqual(self.numbers("open=false"), ["D", "C"])
        self.assertEqual(self.numbers("status=READY"), ["B"])
        self.assertEqual(self.numbers("search=330 441"), ["A"])
        self.assertEqual(self.numbers("search=tl-0118"), ["B"])
        self.assertEqual(self.numbers("search=chip"), ["C"])

    def test_only_an_admin_deletes_a_claim(self):
        claim = self.claim()
        self.as_seller()
        self.assertEqual(self.client.delete(f"{URL}{claim.pk}/").status_code, 403)
        self.as_admin()
        self.assertEqual(self.client.delete(f"{URL}{claim.pk}/").status_code, 204)
        self.assertFalse(WarrantyClaim.objects.exists())

    def test_no_warranty_counter_is_seeded(self):
        # A claim is known by the number on its warranty card.
        self.assertFalse(DocumentCounter.objects.filter(doc_type="WARRANTY").exists())
        self.assertEqual(DocumentCounter.objects.count(), 9)
