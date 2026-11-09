import uuid

from django.test import TestCase

from module.microsoft_connector.domain.entities import ConnectorEntity
from module.microsoft_onedrive.application.services import (
    NotRevocable, PermissionNotFound, SharingService, ConnectorUnavailable,
)
from module.microsoft_onedrive.domain.ports import DriveGateway
from module.microsoft_onedrive.domain.sharing import Risk, classify, parse_permission, summarize
from module.microsoft_onedrive.infrastructure.repositories import DjangoSharingScanRepository
from module.microsoft_onedrive.infrastructure.scan_models import SharingAction
from shared_kernel.graph_rest import safe_id

DOMAINS = ["cimtech.ca"]


def link(scope, type_="view", **kw):
    return {"id": "p1", "roles": ["write" if type_ == "edit" else "read"],
            "link": {"scope": scope, "type": type_, "webUrl": "https://x/s/abc"}, **kw}


class ParsingTests(TestCase):
    def risk(self, p):
        return classify(parse_permission(p, DOMAINS))

    def test_anonymous_links(self):
        self.assertEqual(self.risk(link("anonymous", "edit")), Risk.CRITICAL)
        self.assertEqual(self.risk(link("anonymous", "view")), Risk.HIGH)
        self.assertEqual(self.risk(link("anonymous", "view", expirationDateTime="2026-12-01T00:00:00Z")), Risk.MEDIUM)

    def test_organization_links(self):
        self.assertEqual(self.risk(link("organization", "view")), Risk.LOW)
        self.assertEqual(self.risk(link("organization", "edit")), Risk.MEDIUM)

    def test_specific_people_link_is_info_unless_external(self):
        internal = link("users", grantedToIdentitiesV2=[{"user": {"email": "bob@cimtech.ca"}}])
        external = link("users", grantedToIdentitiesV2=[{"user": {"email": "eve@gmail.com"}}])
        self.assertEqual(self.risk(internal), Risk.INFO)
        self.assertEqual(self.risk(external), Risk.MEDIUM)
        self.assertTrue(parse_permission(external, DOMAINS).external)

    def test_guest_login_marks_external(self):
        p = {"id": "d", "roles": ["read"],
             "grantedToV2": {"siteUser": {"displayName": "Eve", "loginName": "i:0#.f|membership|eve_x.com#ext#@t.onmicrosoft.com"}}}
        e = parse_permission(p, DOMAINS)
        self.assertEqual((e.kind, e.external), ("direct", True))

    def test_owner_and_inherited_are_ignored(self):
        self.assertIsNone(parse_permission({"id": "o", "roles": ["owner"],
                                            "grantedToV2": {"user": {"email": "me@cimtech.ca"}}}, DOMAINS))
        self.assertIsNone(parse_permission({**link("anonymous"), "inheritedFrom": {"id": "parent"}}, DOMAINS))

    def test_summary(self):
        recs = [{"risk": "critical", "scope": "anonymous", "link_type": "edit", "external": False, "expires_at": None},
                {"risk": "low", "scope": "organization", "link_type": "view", "external": False, "expires_at": None},
                {"risk": "medium", "scope": "direct", "link_type": "", "external": True, "expires_at": None}]
        s = summarize(recs)
        self.assertEqual((s["total"], s["anonymous"], s["organization"], s["external"], s["anonymous_edit"]),
                         (3, 1, 1, 1, 1))
        self.assertEqual(s["by_risk"]["critical"], 1)


class SafeIdTests(TestCase):
    def test_rejects_path_injection(self):
        for bad in ("../x", "a/b", "a?b=1", "", "a b", "x" * 600, "a..b"):
            with self.assertRaises(ValueError):
                safe_id(bad)
        self.assertEqual(safe_id("b!AbC-123_x=="), "b!AbC-123_x==")


class FakeGateway(DriveGateway):
    def __init__(self):
        self.deleted = []
        self.perms = {
            "f1": [link("anonymous", "edit", id="pa"), {"id": "po", "roles": ["owner"],
                   "grantedToV2": {"user": {"email": "me@cimtech.ca"}}}],
            "f2": [{**link("anonymous"), "id": "pi", "inheritedFrom": {"id": "x"}}],
            "f3": [link("organization", id="pg")],
            # « anonyme » selon Graph, mais fichier partagé avec des personnes précises (lien Teams)
            "f4": [link("anonymous", "edit", id="pt", grantedToIdentitiesV2=[{"user": {"email": "bob@cimtech.ca"}}])],
        }
        self.scopes = {"f1": "anonymous", "f4": "users"}

    async def internal_domains(self): return DOMAINS
    async def list_drives(self): return [{"id": "d1", "label": "OneDrive — Bob", "owner": "bob@cimtech.ca", "kind": "onedrive"},
                                         {"id": "d2", "label": "Site — Docs", "owner": "", "kind": "sharepoint"}]
    async def children(self, drive_id, parent_id): return []
    async def permissions(self, drive_id, item_id): return self.perms.get(item_id, [])
    async def scan_items(self, drive_id, max_items):
        if drive_id == "d2":
            raise PermissionError("403 Sites")
        return ([{"id": "f1", "name": "secret.xlsx", "path": "/RH/secret.xlsx", "webUrl": "https://x/f1"},
                 {"id": "f2", "name": "child.docx", "path": "/RH/child.docx", "webUrl": "https://x/f2"},
                 {"id": "f3", "name": "Public", "folder": {}, "path": "/Public", "webUrl": "https://x/f3"},
                 {"id": "f4", "name": "chat.pdf", "path": "/Teams/chat.pdf", "webUrl": "https://x/f4"}], False)
    async def permissions_batch(self, drive_id, item_ids): return [self.perms.get(i, []) for i in item_ids]
    async def shared_scopes(self, drive_id, item_ids): return {i: self.scopes.get(i) for i in item_ids}
    async def delete_permission(self, drive_id, item_id, permission_id): self.deleted.append((item_id, permission_id))


class _Connectors:
    def __init__(self, c): self.c = c
    def get(self, _id): return self.c
    def list_active(self): return [self.c]


class ServiceTests(TestCase):
    def setUp(self):
        self.c = ConnectorEntity(org_id=uuid.uuid4(), name="c", tenant_id=uuid.uuid4(),
                                 client_id=uuid.uuid4(), client_secret="s")
        self.gw = FakeGateway()
        self.svc = SharingService(connectors=_Connectors(self.c), scans=DjangoSharingScanRepository(),
                                  gateway_factory=lambda c: self.gw)

    def scan(self):
        scan, created = self.svc.start_scan(self.c.id)
        self.assertTrue(created)
        return self.svc.run_scan(scan["id"], self.c.id)

    def test_scan_collects_risky_shares_sorted_and_isolates_drive_errors(self):
        done = self.scan()
        self.assertEqual(done["status"], "done")
        names = [(r["name"], r["risk"]) for r in done["records"]]
        # propriétaire + hérité ignorés ; le lien « anonyme » Teams (shared.scope=users) est déclassé
        self.assertEqual(names, [("secret.xlsx", "critical"), ("Public", "low"), ("chat.pdf", "info")])
        self.assertEqual(done["summary"]["anonymous_edit"], 1)
        self.assertEqual(len(done["errors"]), 1)  # le drive SharePoint illisible n'a pas fait échouer l'audit
        self.assertIn("Site — Docs", done["errors"][0])

    def test_anonymous_link_on_item_shared_with_specific_people_is_downgraded(self):
        done = self.scan()
        chat = next(r for r in done["records"] if r["name"] == "chat.pdf")
        self.assertEqual((chat["scope"], chat["risk"]), ("users", "info"))
        self.assertIn("shared.scope", chat["note"])
        secret = next(r for r in done["records"] if r["name"] == "secret.xlsx")
        self.assertEqual((secret["scope"], secret["note"]), ("anonymous", ""))  # confirmé anonyme : alerte conservée

    def test_unknown_scope_keeps_the_alert(self):
        self.gw.scopes = {}  # facette illisible : par prudence, on ne déclasse pas
        done = self.scan()
        self.assertEqual(next(r for r in done["records"] if r["name"] == "chat.pdf")["risk"], "critical")

    def test_explorer_permissions_also_reconcile(self):
        perm = self.svc.permissions(self.c.id, "d1", "f4")[0]
        self.assertEqual((perm["scope"], perm["risk"]), ("users", "info"))

    def test_only_one_scan_runs_at_a_time(self):
        scan, created = self.svc.start_scan(self.c.id)
        again, created2 = self.svc.start_scan(self.c.id)
        self.assertEqual((created, created2, scan["id"]), (True, False, again["id"]))

    def test_revoke_deletes_logs_and_updates_latest_scan(self):
        self.scan()
        self.svc.revoke(self.c.id, "d1", "f1", "pa", by="admin")
        self.assertEqual(self.gw.deleted, [("f1", "pa")])
        self.assertTrue(SharingAction.objects.filter(permission_id="pa", ok=True, performed_by="admin").exists())
        latest = self.svc.latest(self.c.id)
        self.assertEqual([r["name"] for r in latest["records"]], ["Public", "chat.pdf"])  # seul « secret.xlsx » a disparu
        self.assertEqual(latest["summary"]["anonymous"], 0)

    def test_revoke_refuses_unknown_owner_and_inherited(self):
        with self.assertRaises(PermissionNotFound):
            self.svc.revoke(self.c.id, "d1", "f1", "nope", by="admin")
        with self.assertRaises(NotRevocable):
            self.svc.revoke(self.c.id, "d1", "f1", "po", by="admin")
        with self.assertRaises(NotRevocable):
            self.svc.revoke(self.c.id, "d1", "f2", "pi", by="admin")
        self.assertEqual(self.gw.deleted, [])

    def test_permissions_view_hides_owner_and_flags_inherited(self):
        self.gw.perms["f2"].append(link("organization", id="pz"))
        perms = self.svc.permissions(self.c.id, "d1", "f2")
        self.assertEqual({(p["permission_id"], p["inherited"]) for p in perms}, {("pi", True), ("pz", False)})

    def test_inactive_connector(self):
        svc = SharingService(connectors=type("C", (), {"get": lambda s, i: None})(), scans=DjangoSharingScanRepository())
        with self.assertRaises(ConnectorUnavailable):
            svc.drives(uuid.uuid4())
