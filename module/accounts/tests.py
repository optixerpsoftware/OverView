import os
import time
import uuid
from unittest import mock

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import AsyncClient, SimpleTestCase, TransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken

from module.accounts.application.service import AccountError, AccountService, AuthFailed, check_access
from module.accounts.domain.access import allowed, required_role, verify_totp
from module.accounts.infrastructure.models import OperatorProfile
from module.organization.infrastructure.models import Organization
from shared_kernel.totp import totp_code

T = 1_800_000_000.0
PW = "Un-mot-de-passe-solide-42"


class AccessTests(SimpleTestCase):
    def test_role_matrix(self):
        self.assertTrue(allowed("viewer", "GET", "/api/insights/dashboard/"))
        self.assertFalse(allowed("viewer", "POST", "/api/insights/alerts/status/"))
        self.assertTrue(allowed("operator", "POST", "/api/identity/mailboxes/x/emergency/"))
        self.assertFalse(allowed("operator", "PUT", "/api/protection/organizations/x/"))
        self.assertFalse(allowed("operator", "DELETE", "/api/organizations/x/"))
        self.assertFalse(allowed("operator", "PUT", "/api/identity/connectors/x/conditional-access/security-defaults/"))
        self.assertTrue(allowed("admin", "PUT", "/api/protection/organizations/x/"))
        self.assertTrue(allowed("viewer", "POST", "/api/accounts/me/mfa/"))        # son propre MFA
        self.assertEqual(required_role("GET", "/api/accounts/users/"), "admin")

    def test_totp_window_and_replay(self):
        secret = "JBSWY3DPEHPK3PXP"
        code = totp_code(secret, at=T)
        c = verify_totp(secret, code, T)
        self.assertIsNotNone(c)
        self.assertIsNone(verify_totp(secret, code, T, last_counter=c))           # rejeu refusé
        self.assertIsNotNone(verify_totp(secret, totp_code(secret, at=T - 30), T))  # tolérance d'un pas
        self.assertIsNone(verify_totp(secret, totp_code(secret, at=T - 120), T))
        self.assertIsNone(verify_totp(secret, "12a456", T))


class ServiceTests(TransactionTestCase):
    def setUp(self):
        self.u = get_user_model().objects.create_user("tech", password=PW)

    def enroll(self, user):
        s = AccountService.mfa_setup(user)
        AccountService.mfa_enable(user, totp_code(s["secret"], at=T), now=T)
        return s["secret"]

    def test_login_without_and_with_mfa(self):
        r = AccountService.login("tech", PW)
        self.assertIn("access", r)
        with self.assertRaises(AuthFailed):
            AccountService.login("tech", "faux")
        secret = self.enroll(self.u)
        r = AccountService.login("tech", PW)
        self.assertEqual(set(r), {"mfa_required", "mfa_token"})                    # aucun jeton avant le code
        with self.assertRaises(AuthFailed):
            AccountService.verify(r["mfa_token"], "000000", now=T + 60)
        tokens = AccountService.verify(r["mfa_token"], totp_code(secret, at=T + 60), now=T + 60)
        self.assertIn("access", tokens)
        with self.assertRaises(AuthFailed):                                          # même code rejoué
            AccountService.verify(r["mfa_token"], totp_code(secret, at=T + 60), now=T + 60)

    def test_lockout_after_five_bad_codes(self):
        self.enroll(self.u)
        tok = AccountService.login("tech", PW)["mfa_token"]
        for _ in range(5):
            with self.assertRaises(AuthFailed):
                AccountService.verify(tok, "000000", now=T + 90)
        self.assertIsNotNone(OperatorProfile.objects.get(user=self.u).locked_until)
        with self.assertRaises(AuthFailed):
            AccountService.login("tech", PW)

    def test_roles_and_last_admin_guard(self):
        admin = get_user_model().objects.create_superuser("root", password=PW)
        self.assertEqual(AccountService.me(self.u)["role"], "viewer")
        AccountService.update_user(admin, self.u.pk, role="operator")
        self.assertEqual(AccountService.me(self.u)["role"], "operator")
        with self.assertRaises(AccountError):
            AccountService.update_user(admin, admin.pk, role="viewer")                # pas soi-même
        AccountService.update_user(admin, self.u.pk, role="admin")
        AccountService.update_user(self.u, admin.pk, role="viewer")
        with self.assertRaises(AccountError):
            AccountService.update_user(admin, self.u.pk, active=False)                 # dernier admin actif
        with self.assertRaises(AccountError):
            AccountService.create_user("x", "123", "viewer")                           # mot de passe trop faible
        self.assertEqual(AccountService.create_user("bob", PW, "operator")["role"], "operator")

    def test_mfa_required_globally(self):
        with mock.patch.dict(os.environ, {"OVERVIEW_REQUIRE_MFA": "true"}):
            denied = check_access(self.u, "GET", "/api/insights/dashboard/")
            self.assertEqual(denied[2], "mfa_enrollment_required")
            self.assertIsNone(check_access(self.u, "POST", "/api/accounts/me/mfa/"))
            self.enroll(self.u)
            self.assertIsNone(check_access(self.u, "GET", "/api/insights/dashboard/"))
            with self.assertRaises(AccountError):
                AccountService.mfa_disable(self.u, "000000")


class ViewTests(TransactionTestCase):
    def setUp(self):
        self.viewer = get_user_model().objects.create_user("lecteur", password=PW)
        self.org = Organization.objects.create(name="O")
        self.c = AsyncClient()

    def h(self, user):
        return {"Authorization": f"Bearer {AccessToken.for_user(user)}"}

    def test_viewer_is_read_only_on_async_and_drf_views(self):
        h = self.h(self.viewer)
        self.assertEqual(async_to_sync(self.c.get)("/api/insights/dashboard/", headers=h).status_code, 200)
        r = async_to_sync(self.c.post)("/api/insights/alerts/status/", {"ids": [], "status": "acknowledged"},
                                       content_type="application/json", headers=h)
        self.assertEqual((r.status_code, r.json()["code"]), (403, "forbidden_role"))
        self.assertEqual(async_to_sync(self.c.get)("/api/organizations/", headers=h).status_code, 200)
        r = async_to_sync(self.c.post)("/api/organizations/", {"name": "X"}, content_type="application/json", headers=h)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(async_to_sync(self.c.get)("/api/accounts/users/", headers=h).status_code, 403)
        self.assertEqual(async_to_sync(self.c.get)("/api/accounts/me/", headers=h).json()["role"], "viewer")

    def test_login_endpoints(self):
        r = async_to_sync(self.c.post)("/api/auth/login/", {"username": "lecteur", "password": PW}, content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("access", r.json())
        self.assertEqual(r["Cache-Control"], "no-store")
        r = async_to_sync(self.c.post)("/api/auth/token/", {"username": "lecteur", "password": "x"}, content_type="application/json")
        self.assertEqual(r.status_code, 401)
        r = async_to_sync(self.c.post)("/api/auth/mfa/verify/", {"mfa_token": "forge", "code": "123456"}, content_type="application/json")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(async_to_sync(self.c.post)("/api/auth/token/refresh/", {"refresh": "x"},
                                                     content_type="application/json").status_code, 401)
