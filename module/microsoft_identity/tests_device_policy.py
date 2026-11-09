import copy
import uuid

from django.test import TestCase

from module.microsoft_connector.domain.entities import ConnectorEntity
from module.microsoft_identity.application.device_policy_service import (
    ConfirmationRequired, ConnectorUnavailable, DevicePolicyService,
)
from module.microsoft_identity.domain import device_policy as dp
from module.microsoft_identity.infrastructure.audit_models import TenantSettingChange

ALL = {"@odata.type": "#microsoft.graph.allDeviceRegistrationMembership"}
RAW = {
    "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#policies/deviceRegistrationPolicy/$entity",
    "id": "deviceRegistrationPolicy", "displayName": "Device Registration Policy", "description": "x",
    "userDeviceQuota": 50, "multiFactorAuthConfiguration": "notRequired",
    "azureADRegistration": {"isAdminConfigurable": False, "allowedToRegister": ALL},
    "azureADJoin": {"isAdminConfigurable": True, "allowedToJoin": ALL},
    "localAdminPassword": {"isEnabled": False},
}


class ParseApplyTests(TestCase):
    def test_parse(self):
        s = dp.parse(RAW)
        self.assertEqual((s.user_device_quota, s.mfa_required, s.join_scope, s.laps_enabled), (50, False, "all", False))
        self.assertFalse(s.register_configurable)

    def test_parse_selected_counts(self):
        raw = copy.deepcopy(RAW)
        raw["azureADJoin"]["allowedToJoin"] = {
            "@odata.type": "#microsoft.graph.enumeratedDeviceRegistrationMembership", "users": ["a", "b"], "groups": ["g"]}
        s = dp.parse(raw)
        self.assertEqual((s.join_scope, s.join_users, s.join_groups), ("selected", 2, 1))

    def test_apply_does_not_mutate_input_and_keeps_unknown_fields(self):
        before = copy.deepcopy(RAW)
        out = dp.apply_changes(RAW, {"user_device_quota": 10, "mfa_required": True, "join_scope": "none", "laps_enabled": True})
        self.assertEqual(RAW, before)
        self.assertEqual((out["userDeviceQuota"], out["multiFactorAuthConfiguration"]), (10, "required"))
        self.assertEqual(out["azureADJoin"]["allowedToJoin"]["@odata.type"], "#microsoft.graph.noDeviceRegistrationMembership")
        self.assertTrue(out["localAdminPassword"]["isEnabled"])
        self.assertEqual(out["displayName"], "Device Registration Policy")   # champs non touchés conservés

    def test_legacy_mfa_encoding_is_preserved(self):
        raw = {**RAW, "multiFactorAuthConfiguration": "0"}
        self.assertEqual(dp.apply_changes(raw, {"mfa_required": True})["multiFactorAuthConfiguration"], "1")

    def test_validation(self):
        for bad in ({"user_device_quota": 0}, {"user_device_quota": 101}, {"user_device_quota": "5"},
                    {"user_device_quota": True}, {"mfa_required": "oui"}, {"join_scope": "selected"},
                    {"join_scope": "tout"}, {"laps_enabled": 1}, {"nimporte": 1}):
            with self.assertRaises(ValueError, msg=str(bad)):
                dp.apply_changes(RAW, bad)

    def test_non_configurable_setting_is_refused_but_unchanged_value_is_fine(self):
        with self.assertRaises(ValueError):
            dp.apply_changes(RAW, {"register_scope": "none"})          # isAdminConfigurable = false
        dp.apply_changes(RAW, {"register_scope": "all"})                # déjà « all » : pas de changement, pas d'erreur

    def test_diff_is_human_readable(self):
        lines = dp.diff(dp.parse(RAW), dp.parse(dp.apply_changes(RAW, {"mfa_required": True, "user_device_quota": 5})))
        self.assertEqual(lines, ["Quota d'appareils par utilisateur : 50 → 5", "MFA exigé à l'inscription : non → oui"])


class FakeGateway:
    def __init__(self):
        self.raw = copy.deepcopy(RAW)
        self.puts = []

    async def get(self): return copy.deepcopy(self.raw)

    async def group_names(self, ids): return {i: "TI" for i in ids}

    async def groups(self, query=""): return [{"id": "11111111-1111-1111-1111-111111111111", "name": "TI"}]

    async def create_security_group(self, name, nickname, description, member_ids):
        self.created = (name, nickname, member_ids)
        return {"id": "33333333-3333-3333-3333-333333333333", "name": name}

    async def directory_users(self): return [{"id": "44444444-4444-4444-4444-444444444444", "name": "Bob", "upn": "bob@x.com"}]

    async def put(self, raw):
        self.puts.append(copy.deepcopy(raw))
        self.raw = raw


class _Connectors:
    def __init__(self, c): self.c = c
    def get(self, _id): return self.c


class ServiceTests(TestCase):
    def setUp(self):
        self.c = ConnectorEntity(org_id=uuid.uuid4(), name="c", tenant_id=uuid.uuid4(),
                                 client_id=uuid.uuid4(), client_secret="s")
        self.gw = FakeGateway()
        self.svc = DevicePolicyService(connectors=_Connectors(self.c), gateway_factory=lambda c: self.gw)

    def test_update_requires_confirmation_word(self):
        for bad in (None, "", "modifier", "OK"):
            with self.assertRaises(ConfirmationRequired):
                self.svc.update(self.c.id, {"user_device_quota": 5}, "admin", bad)
        self.assertEqual(self.gw.puts, [])

    def test_update_writes_full_policy_and_logs(self):
        settings, lines = self.svc.update(self.c.id, {"mfa_required": True}, "admin", "MODIFIER")
        self.assertTrue(settings.mfa_required)
        self.assertEqual(lines, ["MFA exigé à l'inscription : non → oui"])
        self.assertEqual(len(self.gw.puts), 1)
        self.assertEqual(self.gw.puts[0]["userDeviceQuota"], 50)          # le reste de la politique est renvoyé intact
        log = TenantSettingChange.objects.get()
        self.assertEqual((log.ok, log.performed_by, log.setting), (True, "admin", "device_registration"))

    def test_no_difference_means_no_write_and_no_log(self):
        _, lines = self.svc.update(self.c.id, {"user_device_quota": 50}, "admin", "MODIFIER")
        self.assertEqual(lines, [])
        self.assertEqual(self.gw.puts, [])
        self.assertEqual(TenantSettingChange.objects.count(), 0)

    def test_failed_write_is_logged_and_raised(self):
        async def boom(raw): raise RuntimeError("403")
        self.gw.put = boom
        with self.assertRaises(RuntimeError):
            self.svc.update(self.c.id, {"mfa_required": True}, "admin", "MODIFIER")
        self.assertFalse(TenantSettingChange.objects.get().ok)

    def test_invalid_value_never_reaches_graph(self):
        with self.assertRaises(ValueError):
            self.svc.update(self.c.id, {"user_device_quota": 9999}, "admin", "MODIFIER")
        self.assertEqual(self.gw.puts, [])

    def test_inactive_connector(self):
        svc = DevicePolicyService(connectors=type("C", (), {"get": lambda s, i: None})())
        with self.assertRaises(ConnectorUnavailable):
            svc.get(uuid.uuid4())


G1 = "11111111-1111-1111-1111-111111111111"
G2 = "22222222-2222-2222-2222-222222222222"
ENUM = "#microsoft.graph.enumeratedDeviceRegistrationMembership"


class SelectedMembershipTests(TestCase):
    def test_block_everyone_except_a_group(self):
        out = dp.apply_changes(RAW, {"join_scope": "selected", "join_groups": [G1.upper()]})
        m = out["azureADJoin"]["allowedToJoin"]
        self.assertEqual((m["@odata.type"], m["groups"], m["users"]), (ENUM, [G1], []))   # GUID normalisé en minuscules
        s = dp.parse(out)
        self.assertEqual((s.join_scope, s.join_group_ids, s.join_groups), ("selected", (G1,), 1))

    def test_selection_requires_at_least_one_member_and_valid_guids(self):
        with self.assertRaises(ValueError):
            dp.apply_changes(RAW, {"join_scope": "selected"})
        with self.assertRaises(ValueError):
            dp.apply_changes(RAW, {"join_scope": "selected", "join_groups": []})
        with self.assertRaises(ValueError):
            dp.apply_changes(RAW, {"join_scope": "selected", "join_groups": ["pas-un-guid"]})
        with self.assertRaises(ValueError):
            dp.apply_changes(RAW, {"join_scope": "selected", "join_groups": ["x" * 36]})

    def test_groups_only_make_sense_with_selected(self):
        with self.assertRaises(ValueError):
            dp.apply_changes(RAW, {"join_scope": "none", "join_groups": [G1]})
        with self.assertRaises(ValueError):
            dp.apply_changes(RAW, {"join_groups": [G1]})        # le réglage courant est « all »

    def test_editing_the_group_list_keeps_existing_users_and_reports_the_change(self):
        raw = copy.deepcopy(RAW)
        raw["azureADJoin"]["allowedToJoin"] = {"@odata.type": ENUM, "users": [G2], "groups": [G1]}
        out = dp.apply_changes(raw, {"join_groups": [G1, G2]})
        self.assertEqual(out["azureADJoin"]["allowedToJoin"]["users"], [G2])
        lines = dp.diff(dp.parse(raw), dp.parse(out))
        self.assertEqual(len(lines), 1)
        self.assertIn("1 groupe", lines[0])
        self.assertIn("2 groupes", lines[0])

    def test_switching_to_all_drops_the_selection_and_diff_names_it(self):
        raw = dp.apply_changes(RAW, {"join_scope": "selected", "join_groups": [G1]})
        out = dp.apply_changes(raw, {"join_scope": "all"})
        self.assertEqual(out["azureADJoin"]["allowedToJoin"], {"@odata.type": "#microsoft.graph.allDeviceRegistrationMembership"})
        self.assertEqual(dp.diff(dp.parse(raw), dp.parse(out)),
                         ["Qui peut joindre des appareils : une sélection (1 groupe, 0 utilisateur) → tous les utilisateurs"])

    def test_non_configurable_join_refuses_selection(self):
        raw = copy.deepcopy(RAW)
        raw["azureADJoin"]["isAdminConfigurable"] = False
        with self.assertRaises(ValueError):
            dp.apply_changes(raw, {"join_scope": "selected", "join_groups": [G1]})


class GroupServiceTests(TestCase):
    def test_service_exposes_selected_group_names(self):
        c = ConnectorEntity(org_id=uuid.uuid4(), name="c", tenant_id=uuid.uuid4(), client_id=uuid.uuid4(), client_secret="s")
        gw = FakeGateway()
        gw.raw["azureADJoin"]["allowedToJoin"] = {"@odata.type": ENUM, "users": [], "groups": [G1]}
        svc = DevicePolicyService(connectors=_Connectors(c), gateway_factory=lambda x: gw)
        settings, groups = svc.get_with_groups(c.id)
        self.assertEqual(groups, [{"id": G1, "name": "TI"}])
        self.assertEqual(svc.groups(c.id), [{"id": G1, "name": "TI"}])


class CreateGroupTests(TestCase):
    def setUp(self):
        self.c = ConnectorEntity(org_id=uuid.uuid4(), name="c", tenant_id=uuid.uuid4(), client_id=uuid.uuid4(), client_secret="s")
        self.gw = FakeGateway()
        self.svc = DevicePolicyService(connectors=_Connectors(self.c), gateway_factory=lambda x: self.gw)

    def test_creates_security_group_and_logs(self):
        g = self.svc.create_group(self.c.id, "TI - jonction d'appareils", [G1.upper(), G1], "admin")
        self.assertEqual(g["id"], "33333333-3333-3333-3333-333333333333")
        name, nickname, members = self.gw.created
        self.assertEqual(members, [G1])                                  # doublons retirés, GUID normalisé
        self.assertRegex(nickname, r"^[a-z0-9]+-[0-9a-f]{6}$")           # nickname technique sûr
        log = TenantSettingChange.objects.get(setting="security_group")
        self.assertTrue(log.ok)
        self.assertIn("1 membre", log.changes[0])

    def test_validation(self):
        for name, members in (("ab", [G1]), ("x" * 65, [G1]), ("TI", []), ("TI team", ["pas-un-guid"]),
                              ("TI team", [G1] * 0), ("TI team", None)):
            with self.assertRaises(ValueError, msg=str((name, members))):
                self.svc.create_group(self.c.id, name, members, "admin")
        with self.assertRaises(ValueError):
            self.svc.create_group(self.c.id, "TI team", [f"{i:08d}-0000-0000-0000-000000000000" for i in range(21)], "admin")
        self.assertFalse(hasattr(self.gw, "created"))

    def test_failed_creation_is_logged(self):
        async def boom(*a): raise RuntimeError("403")
        self.gw.create_security_group = boom
        with self.assertRaises(RuntimeError):
            self.svc.create_group(self.c.id, "TI team", [G1], "admin")
        self.assertFalse(TenantSettingChange.objects.get(setting="security_group").ok)

    def test_history_includes_group_creation(self):
        self.svc.create_group(self.c.id, "TI team", [G1], "admin")
        self.assertEqual(len(self.svc.history(self.c.id)), 1)
