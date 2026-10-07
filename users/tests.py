# v1.0.3 — the auth rules that matter, tested
import time
from datetime import timedelta

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from users.models import Role, User
from users.scopes import has_scope


class AuthFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller = User.objects.create_user(
            username="sokha", password="counter-pass-99", full_name="Sokha Chan",
            role=Role.SELLER,
        )

    def test_login_returns_access_and_sets_httponly_cookie(self):
        res = self.client.post(
            reverse("auth-login"),
            {"username": "sokha", "password": "counter-pass-99"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn("access", res.data)
        cookie = res.cookies[settings.AUTH_COOKIE_NAME]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["path"], settings.AUTH_COOKIE_PATH)
        # the refresh token must never appear in the body
        self.assertNotIn("refresh", res.data)

    def test_login_rejects_inactive_user(self):
        self.seller.is_active = False
        self.seller.save()
        res = self.client.post(
            reverse("auth-login"),
            {"username": "sokha", "password": "counter-pass-99"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_refresh_rotates_and_old_token_stops_working(self):
        login = self.client.post(
            reverse("auth-login"),
            {"username": "sokha", "password": "counter-pass-99"},
            format="json",
        )
        first = login.cookies[settings.AUTH_COOKIE_NAME].value

        res = self.client.post(reverse("auth-refresh"))
        self.assertEqual(res.status_code, 200)
        second = res.cookies[settings.AUTH_COOKIE_NAME].value
        self.assertNotEqual(first, second)

        # replaying the first token must fail
        self.client.cookies[settings.AUTH_COOKIE_NAME] = first
        replay = self.client.post(reverse("auth-refresh"))
        self.assertEqual(replay.status_code, 401)

    def test_refresh_without_cookie_is_unauthorised(self):
        self.assertEqual(self.client.post(reverse("auth-refresh")).status_code, 401)

    def test_logout_clears_the_cookie(self):
        self.client.post(
            reverse("auth-login"),
            {"username": "sokha", "password": "counter-pass-99"},
            format="json",
        )
        res = self.client.post(reverse("auth-logout"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.cookies[settings.AUTH_COOKIE_NAME].value, "")

    def test_absolute_cap_ends_the_session(self):
        from users.tokens import issue_refresh, rotate_refresh
        from rest_framework_simplejwt.exceptions import TokenError

        refresh = issue_refresh(self.seller)
        over = int(time.time()) - (settings.AUTH_ABSOLUTE_SESSION_HOURS + 1) * 3600
        refresh["auth_time"] = over
        with self.assertRaises(TokenError):
            rotate_refresh(str(refresh))

    def test_me_lists_the_scopes_for_the_role(self):
        login = self.client.post(
            reverse("auth-login"),
            {"username": "sokha", "password": "counter-pass-99"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        res = self.client.get(reverse("auth-me"))
        self.assertEqual(res.status_code, 200)
        self.assertIn("report.sales.own", res.data["scopes"])
        self.assertNotIn("report.receivables", res.data["scopes"])
        self.assertNotIn("stock.post", res.data["scopes"])


class RoleScopeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="bopha", password="owner-pass-99", full_name="Bopha Ly",
            role=Role.ADMIN,
        )
        self.seller = User.objects.create_user(
            username="dara", password="counter-pass-99", full_name="Dara Meas",
            role=Role.SELLER,
        )

    def test_seller_has_no_stock_or_receivables(self):
        self.assertFalse(has_scope(self.seller, "stock.post"))
        self.assertFalse(has_scope(self.seller, "report.receivables"))
        self.assertFalse(has_scope(self.seller, "company.edit"))

    def test_seller_may_sell_quote_and_take_payment(self):
        for scope in ["sell", "quotation.edit", "payment.record", "return.create"]:
            self.assertTrue(has_scope(self.seller, scope), scope)

    def test_seller_void_is_limited_to_their_own(self):
        self.assertTrue(has_scope(self.seller, "invoice.void.own"))
        self.assertFalse(has_scope(self.seller, "invoice.void.any"))
        self.assertTrue(has_scope(self.admin, "invoice.void.any"))

    def test_only_an_admin_voids_a_payment(self):
        self.assertTrue(has_scope(self.admin, "payment.void"))
        self.assertFalse(has_scope(self.seller, "payment.void"))

    def test_only_an_admin_deletes_a_warranty_claim(self):
        self.assertTrue(has_scope(self.admin, "warranty.delete"))
        self.assertFalse(has_scope(self.seller, "warranty.delete"))
        self.assertTrue(has_scope(self.seller, "warranty.edit"))

    def test_only_admin_manages_users(self):
        client = APIClient()
        login = client.post(
            reverse("auth-login"),
            {"username": "dara", "password": "counter-pass-99"},
            format="json",
        )
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        self.assertEqual(client.get("/api/users/").status_code, 403)

    def test_admin_cannot_switch_off_their_own_account(self):
        client = APIClient()
        login = client.post(
            reverse("auth-login"),
            {"username": "bopha", "password": "owner-pass-99"},
            format="json",
        )
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        res = client.post(f"/api/users/{self.admin.pk}/deactivate/")
        self.assertEqual(res.status_code, 400)


class VoidWindowTests(TestCase):
    """CanVoidInvoice without the sales app: a stand-in object with the fields
    the rule reads."""

    def setUp(self):
        from django.utils import timezone

        self.now = timezone.now
        self.admin = User.objects.create_user(
            username="bopha2", password="owner-pass-99", full_name="Bopha Ly",
            role=Role.ADMIN,
        )
        self.seller = User.objects.create_user(
            username="dara2", password="counter-pass-99", full_name="Dara Meas",
            role=Role.SELLER,
        )
        self.other = User.objects.create_user(
            username="sokha2", password="counter-pass-99", full_name="Sokha Chan",
            role=Role.SELLER,
        )

    def _invoice(self, seller, when=None, status="COMPLETED"):
        from django.utils import timezone

        class Stub:
            pass

        inv = Stub()
        inv.seller_id = seller.id
        inv.sale_date = when or timezone.now()
        inv.status = status
        return inv

    def _check(self, user, invoice):
        from users.permissions import CanVoidInvoice

        class Req:
            pass

        req = Req()
        req.user = user
        return CanVoidInvoice().has_object_permission(req, None, invoice)

    def test_seller_may_void_their_own_today(self):
        self.assertTrue(self._check(self.seller, self._invoice(self.seller)))

    def test_seller_may_not_void_someone_elses(self):
        self.assertFalse(self._check(self.other, self._invoice(self.seller)))

    def test_seller_may_not_void_yesterday(self):
        from django.utils import timezone

        yesterday = timezone.now() - timedelta(days=1)
        self.assertFalse(self._check(self.seller, self._invoice(self.seller, yesterday)))

    def test_admin_may_void_an_older_invoice(self):
        from django.utils import timezone

        yesterday = timezone.now() - timedelta(days=1)
        self.assertTrue(self._check(self.admin, self._invoice(self.seller, yesterday)))

    def test_an_already_voided_invoice_cannot_be_voided_again(self):
        inv = self._invoice(self.seller, status="VOIDED")
        self.assertFalse(self._check(self.admin, inv))


class UserCreationTests(TestCase):
    """The create and reset paths actually run — they generate a password, and
    Django 5.1 removed the manager helper that used to do it."""

    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="owner", password="owner-pass-99", full_name="Bopha Ly",
            role=Role.ADMIN,
        )
        login = self.client.post(
            reverse("auth-login"),
            {"username": "owner", "password": "owner-pass-99"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

    def test_create_returns_a_one_time_password_and_forces_a_change(self):
        res = self.client.post(
            "/api/users/",
            {"username": "newseller", "full_name": "Chea Sophal", "role": "SELLER"},
            format="json",
        )
        self.assertEqual(res.status_code, 201)
        self.assertIn("initial_password", res.data)
        self.assertGreaterEqual(len(res.data["initial_password"]), 8)

        created = User.objects.get(username="newseller")
        self.assertTrue(created.must_change_password)
        self.assertTrue(created.check_password(res.data["initial_password"]))
        self.assertEqual(created.created_by, self.admin)

    def test_reset_password_issues_a_new_one(self):
        seller = User.objects.create_user(
            username="dara3", password="old-pass-9999", full_name="Dara Meas",
        )
        res = self.client.post(f"/api/users/{seller.pk}/reset-password/")
        self.assertEqual(res.status_code, 200)
        seller.refresh_from_db()
        self.assertTrue(seller.check_password(res.data["initial_password"]))
        self.assertTrue(seller.must_change_password)

    def test_changing_password_clears_the_must_change_flag(self):
        seller = User.objects.create_user(
            username="dara4", password="old-pass-9999", full_name="Dara Meas",
        )
        client = APIClient()
        login = client.post(
            reverse("auth-login"),
            {"username": "dara4", "password": "old-pass-9999"},
            format="json",
        )
        self.assertTrue(login.data["user"]["must_change_password"])
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        res = client.post(
            reverse("auth-password"),
            {"current_password": "old-pass-9999", "new_password": "fresh-pass-8877"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        seller.refresh_from_db()
        self.assertFalse(seller.must_change_password)
        self.assertTrue(seller.check_password("fresh-pass-8877"))

    def test_weak_password_is_rejected(self):
        res = self.client.post(
            "/api/users/",
            {"username": "weak", "full_name": "Test", "role": "SELLER",
             "password": "12345678"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
