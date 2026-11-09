import uuid
from datetime import datetime, timedelta, timezone

from django.test import TestCase

from module.microsoft_compliance.application.services import (
    ComplianceService, evaluate, summarize_licenses,
)
from module.microsoft_compliance.domain import checks
from module.microsoft_compliance.domain.entities import Finding, Category, Severity, Status
from module.microsoft_compliance.domain.ports import ComplianceGateway, DataUnavailable
from module.microsoft_compliance.domain.scoring import compute_score
from module.microsoft_compliance.infrastructure.repositories import DjangoSnapshotRepository
from module.microsoft_connector.domain.entities import ConnectorEntity

NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)
iso = lambda days: (NOW - timedelta(days=days)).isoformat()


def user(upn, **kw):
    return {"userPrincipalName": upn, "userType": "Member", "accountEnabled": True, **kw}


class MfaChecks(TestCase):
    def test_coverage_partial(self):
        users = [user("a@x"), user("b@x"), user("c@x"), user("d@x")]
        regs = [{"userPrincipalName": "a@x", "isMfaRegistered": True},
                {"userPrincipalName": "B@X", "isMfaRegistered": True}]   # insensible à la casse
        f = checks.mfa_coverage(users, regs)
        self.assertEqual(f.status, Status.FAIL)
        self.assertAlmostEqual(f.score, 0.5)
        self.assertEqual(f.details, ("c@x", "d@x"))

    def test_coverage_ignores_guests_and_disabled(self):
        users = [user("a@x"), user("g@x", userType="Guest"), user("off@x", accountEnabled=False)]
        regs = [{"userPrincipalName": "a@x", "isMfaRegistered": True}]
        self.assertEqual(checks.mfa_coverage(users, regs).status, Status.PASS)

    def test_admin_without_mfa_is_critical_fail(self):
        regs = [{"userPrincipalName": "boss@x", "isAdmin": True, "isMfaRegistered": False},
                {"userPrincipalName": "ok@x", "isAdmin": True, "isMfaRegistered": True}]
        f = checks.mfa_admins(regs)
        self.assertEqual((f.status, f.severity, f.details), (Status.FAIL, Severity.CRITICAL, ("boss@x",)))

    def test_weak_methods_only(self):
        regs = [{"userPrincipalName": "sms@x", "isMfaRegistered": True, "methodsRegistered": ["mobilePhone"]},
                {"userPrincipalName": "strong@x", "isMfaRegistered": True,
                 "methodsRegistered": ["mobilePhone", "microsoftAuthenticatorPush"]}]
        f = checks.mfa_weak_methods(regs)
        self.assertEqual(f.details, ("sms@x",))
        self.assertAlmostEqual(f.score, 0.5)


class AccessChecks(TestCase):
    def roles(self, n):
        return [{"displayName": "Global Administrator",
                 "members": [{"userPrincipalName": f"ga{i}@x"} for i in range(n)]}]

    def test_global_admin_count_bounds(self):
        self.assertEqual(checks.global_admin_count(self.roles(1)).status, Status.FAIL)
        self.assertEqual(checks.global_admin_count(self.roles(3)).status, Status.PASS)
        f = checks.global_admin_count(self.roles(8))
        self.assertEqual((f.status, f.severity), (Status.FAIL, Severity.HIGH))

    def test_guest_admin_detected(self):
        roles = [{"displayName": "Security Administrator",
                  "members": [{"userPrincipalName": "bob_ext.com#EXT#@t.onmicrosoft.com"}]}]
        self.assertEqual(checks.admin_guests(roles).status, Status.FAIL)

    def test_stale_needs_sign_in_activity(self):
        self.assertEqual(checks.stale_accounts([user("a@x")], NOW).status, Status.UNAVAILABLE)

    def test_stale_accounts(self):
        users = [
            user("recent@x", signInActivity={"lastSignInDateTime": iso(5)}),
            user("old@x", signInActivity={"lastSignInDateTime": iso(200)}),
            user("noninteractive@x", signInActivity={"lastSignInDateTime": iso(200),
                                                      "lastNonInteractiveSignInDateTime": iso(2)}),
            user("never@x", signInActivity={}, createdDateTime=iso(400)),
            user("newbie@x", signInActivity={}, createdDateTime=iso(3)),
        ]
        f = checks.stale_accounts(users, NOW)
        self.assertEqual(sorted(d.split(" — ")[0] for d in f.details), ["never@x", "old@x"])


class PolicyChecks(TestCase):
    mfa_all = {"displayName": "MFA tous", "state": "enabled",
               "conditions": {"users": {"includeUsers": ["All"]}, "applications": {"includeApplications": ["All"]}},
               "grantControls": {"builtInControls": ["mfa"]}}
    block_legacy = {"displayName": "Bloquer legacy", "state": "enabled",
                    "conditions": {"clientAppTypes": ["exchangeActiveSync", "other"]},
                    "grantControls": {"builtInControls": ["block"]}}

    def test_security_defaults_cover_everything(self):
        sd = {"isEnabled": True}
        for fn in (checks.baseline_protection, checks.ca_mfa_for_all, checks.ca_block_legacy):
            self.assertEqual(fn(sd, []).status, Status.PASS)

    def test_no_protection_at_all_is_critical(self):
        f = checks.baseline_protection({"isEnabled": False}, [])
        self.assertEqual((f.status, f.severity), (Status.FAIL, Severity.CRITICAL))

    def test_unreadable_conditional_access_is_unknown_not_failure(self):
        # Sans licence P1 on ne peut pas lire l'accès conditionnel : on ne conclut pas « aucune protection ».
        for fn in (checks.baseline_protection, checks.ca_mfa_for_all, checks.ca_block_legacy):
            self.assertEqual(fn({"isEnabled": False}, None).status, Status.UNAVAILABLE)

    def test_mfa_policy_enforced_vs_report_only(self):
        self.assertEqual(checks.ca_mfa_for_all({"isEnabled": False}, [self.mfa_all]).status, Status.PASS)
        report = {**self.mfa_all, "state": "enabledForReportingButNotEnforced"}
        f = checks.ca_mfa_for_all({"isEnabled": False}, [report])
        self.assertEqual((f.status, f.score), (Status.FAIL, 0.3))

    def test_legacy_auth_block(self):
        self.assertEqual(checks.ca_block_legacy({}, [self.block_legacy]).status, Status.PASS)
        self.assertEqual(checks.ca_block_legacy({}, [self.mfa_all]).status, Status.FAIL)


class PostureChecks(TestCase):
    def test_secure_score_with_top_gaps(self):
        score = {"currentScore": 30, "maxScore": 100,
                 "controlScores": [{"controlName": "A", "score": 0}, {"controlName": "B", "score": 5}]}
        profiles = [{"id": "A", "title": "Activer le MFA", "maxScore": 10},
                    {"id": "B", "title": "Bloquer legacy", "maxScore": 5}]
        f = checks.secure_score(score, profiles)
        self.assertEqual(f.status, Status.FAIL)
        self.assertEqual(f.details, ("Activer le MFA (+10 pts possibles)",))
        self.assertAlmostEqual(f.score, 0.3)

    def test_secure_score_missing(self):
        self.assertEqual(checks.secure_score(None).status, Status.UNAVAILABLE)

    def test_app_secrets_expired_and_expiring(self):
        apps = [{"displayName": "OverView", "passwordCredentials": [
                    {"displayName": "ancien", "endDateTime": iso(10)},        # expiré
                    {"displayName": "bientôt", "endDateTime": iso(-10)},      # expire dans 10 j
                    {"displayName": "loin", "endDateTime": iso(-300)}]}]
        f = checks.app_secrets(apps, NOW)
        self.assertEqual((f.status, f.severity), (Status.FAIL, Severity.HIGH))
        self.assertEqual(len(f.details), 2)
        self.assertTrue(f.details[0].endswith("expiré depuis 10 j"))


class ScoringTests(TestCase):
    def f(self, sev, status, score):
        return Finding("c", Category.MFA, "t", sev, status, score=score)

    def test_weighted_and_excludes_unavailable(self):
        fs = [self.f(Severity.CRITICAL, Status.PASS, 1.0),      # 10 pts / 10
              self.f(Severity.HIGH, Status.FAIL, 0.0),          # 0 / 6
              self.f(Severity.MEDIUM, Status.UNAVAILABLE, 0.0),  # ignoré
              self.f(Severity.INFO, Status.PASS, 1.0)]           # poids 0
        self.assertEqual(compute_score(fs), round(100 * 10 / 16, 1))

    def test_none_when_nothing_evaluable(self):
        self.assertIsNone(compute_score([self.f(Severity.HIGH, Status.UNAVAILABLE, 0)]))


class EvaluateTests(TestCase):
    def test_missing_data_makes_checks_unavailable_without_failing_the_scan(self):
        data = {
            "users": [user("a@x")],
            "registrations": DataUnavailable("403", "AuditLog.Read.All"),
            "roles": RuntimeError("boom"),
            "ca_policies": DataUnavailable("pas de P1", "Policy.Read.All"),
            "security_defaults": {"isEnabled": True},
            "secure_score": DataUnavailable("403", "SecurityEvents.Read.All"),
            "score_profiles": [],
            "applications": [],
        }
        by = {f.code: f for f in evaluate(data)}
        self.assertEqual(by["mfa.coverage"].status, Status.UNAVAILABLE)
        self.assertEqual(by["mfa.coverage"].requires, "AuditLog.Read.All")
        self.assertEqual(by["access.global_admins"].status, Status.ERROR)
        self.assertEqual(by["posture.secure_score"].status, Status.UNAVAILABLE)
        # Security Defaults lisible : les contrôles de politiques passent malgré l'absence de P1
        self.assertEqual(by["policies.baseline"].status, Status.PASS)
        self.assertEqual(by["policies.mfa_all"].status, Status.PASS)

    def test_policies_unavailable_when_nothing_readable(self):
        data = {k: DataUnavailable("x") for k in
                ("users", "registrations", "roles", "ca_policies", "security_defaults", "secure_score",
                 "score_profiles", "applications")}
        by = {f.code: f for f in evaluate(data)}
        self.assertEqual(by["policies.baseline"].status, Status.UNAVAILABLE)

    def test_license_summary(self):
        skus = [{"skuPartNumber": "SPB", "servicePlans": [
            {"servicePlanName": "AAD_PREMIUM", "provisioningStatus": "Success"},
            {"servicePlanName": "AAD_PREMIUM_P2", "provisioningStatus": "Disabled"}]}]
        self.assertEqual(summarize_licenses(skus), {"skus": ["SPB"], "entra_p1": True, "entra_p2": False})


class FakeGateway(ComplianceGateway):
    async def licenses(self): return []
    async def users(self): return [user("a@x")]
    async def registrations(self): return [{"userPrincipalName": "a@x", "isMfaRegistered": True}]
    async def roles(self): return [{"displayName": "Global Administrator",
                                    "members": [{"userPrincipalName": "a@x"}, {"userPrincipalName": "b@x"}]}]
    async def ca_policies(self): raise DataUnavailable("pas de P1", "Policy.Read.All")
    async def security_defaults(self): return {"isEnabled": True}
    async def secure_score(self): return {"currentScore": 80, "maxScore": 100, "controlScores": []}
    async def score_profiles(self): return []
    async def applications(self): return []


class _Connectors:
    def __init__(self, c): self.c = c
    def get(self, _id): return self.c
    def list_active(self): return [self.c]


class ServiceTests(TestCase):
    def setUp(self):
        self.c = ConnectorEntity(org_id=uuid.uuid4(), name="c", tenant_id=uuid.uuid4(),
                                 client_id=uuid.uuid4(), client_secret="s")
        self.svc = ComplianceService(connectors=_Connectors(self.c), snapshots=DjangoSnapshotRepository(),
                                     gateway_factory=lambda c: FakeGateway())

    def test_scan_persists_snapshot_and_history(self):
        snap = self.svc.scan(self.c.id)
        # Tout est conforme sauf le Secure Score à 80 % (poids 3 sur 56) : 100 - 0,6/56*100 ≈ 98,9
        self.assertEqual(snap["score"], 98.9)
        codes = {f["code"]: f["status"] for f in snap["findings"]}
        self.assertEqual(codes["access.stale"], "unavailable")   # pas de signInActivity
        self.assertEqual(codes["posture.app_secrets"], "pass")
        self.svc.scan(self.c.id)
        self.assertEqual(len(self.svc.history(self.c.id)), 2)
        self.assertEqual(self.svc.latest(self.c.id)["score"], 98.9)

    def test_total_failure_is_raised_not_stored(self):
        class Broken(FakeGateway):
            async def licenses(self): raise RuntimeError("auth KO")
            async def users(self): raise RuntimeError("auth KO")
            async def registrations(self): raise RuntimeError("auth KO")
            async def roles(self): raise RuntimeError("auth KO")
            async def security_defaults(self): raise RuntimeError("auth KO")
            async def secure_score(self): raise RuntimeError("auth KO")
            async def score_profiles(self): raise RuntimeError("auth KO")
            async def applications(self): raise RuntimeError("auth KO")
            async def ca_policies(self): raise RuntimeError("auth KO")

        svc = ComplianceService(connectors=_Connectors(self.c), snapshots=DjangoSnapshotRepository(),
                                gateway_factory=lambda c: Broken())
        with self.assertRaises(RuntimeError):
            svc.scan(self.c.id)
        self.assertEqual(self.svc.history(self.c.id), [])

    def test_scan_all(self):
        self.assertEqual(self.svc.scan_all(), {"scanned": 1, "failed": 0})


class DeriveTests(TestCase):
    def test_registration_from_methods(self):
        from module.microsoft_compliance.domain.derive import registration_from_methods
        m = lambda t: {"@odata.type": f"#microsoft.graph.{t}"}
        only_password = registration_from_methods("a@x", [m("passwordAuthenticationMethod")], False)
        self.assertFalse(only_password["isMfaRegistered"])
        email_only = registration_from_methods("b@x", [m("passwordAuthenticationMethod"), m("emailAuthenticationMethod")], False)
        self.assertFalse(email_only["isMfaRegistered"])  # le courriel de récupération n'est pas un second facteur
        sms = registration_from_methods("c@x", [m("phoneAuthenticationMethod")], True)
        self.assertEqual((sms["isMfaRegistered"], sms["isAdmin"], sms["methodsRegistered"]), (True, True, ["mobilePhone"]))
        # utilisable tel quel par les contrôles
        self.assertEqual(checks.mfa_weak_methods([sms]).details, ("c@x",))

    def test_activity_report_parsing(self):
        from module.microsoft_compliance.domain.derive import parse_activity_report
        csv_text = ("﻿Report Refresh Date,User Principal Name,Exchange Last Activity Date,Teams Last Activity Date\n"
                    "2026-11-09,Bob@Cimtech.ca,2026-10-01,2026-11-02\n"
                    "2026-11-09,never@cimtech.ca,,\n")
        self.assertEqual(parse_activity_report(csv_text), {"bob@cimtech.ca": "2026-11-02", "never@cimtech.ca": None})

    def test_concealed_names_are_detected(self):
        from module.microsoft_compliance.domain.derive import parse_activity_report
        self.assertIsNone(parse_activity_report(
            "Report Refresh Date,User Principal Name,Exchange Last Activity Date\n2026-11-09,3F2A9C0B7E11D5,2026-11-01\n"))
