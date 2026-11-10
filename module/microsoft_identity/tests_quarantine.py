import uuid

from asgiref.sync import async_to_sync
from django.test import TestCase

from module.microsoft_connector.domain.entities import ConnectorEntity
from module.microsoft_identity.application.quarantine_service import AccountQuarantineService, QuarantineRefused
from module.microsoft_identity.application.services import ConfirmationMismatch, IdentityService
from module.microsoft_identity.infrastructure.audit import DjangoAuditLog
from module.microsoft_identity.infrastructure.audit_models import SecurityAction
from module.microsoft_identity.tests import FakeGateway, _Mailboxes
from module.microsoft_outlook.domain.entities import MailboxEntity


class FakeExchange:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def invoke(self, cmdlet, params):
        if self.fail:
            raise RuntimeError("Exchange 401")
        self.calls.append((cmdlet, params))
        if cmdlet == "Get-CASMailbox":
            return [{"OWAEnabled": True, "ActiveSyncEnabled": True, "ImapEnabled": False, "PopEnabled": False,
                     "MAPIEnabled": True, "EwsEnabled": True}]
        return []


class QuarantineTests(TestCase):
    def setUp(self):
        self.conn = ConnectorEntity(org_id=uuid.uuid4(), name="c", tenant_id=uuid.uuid4(), client_id=uuid.uuid4(), client_secret="s")
        self.mb = MailboxEntity(org_id=self.conn.org_id, connector_id=self.conn.id, user_principal_name="victim@example.com")

        class _C:
            def get(_, cid): return self.conn
        self.gw = FakeGateway()
        self.ident = IdentityService(mailboxes=_Mailboxes(self.mb), connectors=_C(), audit=DjangoAuditLog(),
                                     gateway_factory=lambda c: self.gw, sync_trigger=lambda _: None)

    def svc(self, exo):
        return AccountQuarantineService(identity=self.ident, exchange_factory=lambda c: exo)

    def test_round_trip_restores_original_protocols(self):
        exo = FakeExchange()
        s = self.svc(exo)
        with self.assertRaises(ConfirmationMismatch):
            async_to_sync(s.start_async)(self.mb.id, confirm="autre@x.com", by="admin")
        st = async_to_sync(s.start_async)(self.mb.id, confirm=self.mb.user_principal_name, by="admin", reason="phishing")
        self.assertTrue(st["protocols_saved"])
        self.assertEqual(self.gw.calls[:2], ["enabled:False", "revoke"])
        self.assertEqual(exo.calls[1][1]["OWAEnabled"], False)
        with self.assertRaises(QuarantineRefused):
            async_to_sync(s.start_async)(self.mb.id, confirm=self.mb.user_principal_name, by="admin")
        async_to_sync(s.release_async)(self.mb.id, confirm=self.mb.user_principal_name, by="admin")
        restored = exo.calls[-1][1]
        self.assertEqual((restored["ImapEnabled"], restored["OWAEnabled"]), (False, True))   # état d'origine, pas « tout activé »
        self.assertEqual(self.gw.calls[-1], "enabled:True")
        self.assertIsNone(AccountQuarantineService.status(self.mb.id))
        self.assertEqual(sorted(a.action for a in SecurityAction.objects.all()), ["quarantine:release", "quarantine:start"])

    def test_exchange_unavailable_still_blocks_sign_in(self):
        s = self.svc(FakeExchange(fail=True))
        st = async_to_sync(s.start_async)(self.mb.id, confirm=self.mb.user_principal_name, by="admin")
        self.assertFalse(st["protocols_saved"])
        self.assertIn("NON coupés", st["detail"])
        self.assertIn("enabled:False", self.gw.calls)
        with self.assertRaises(QuarantineRefused):   # déjà en quarantaine
            async_to_sync(s.start_async)(self.mb.id, confirm=self.mb.user_principal_name, by="admin")
        # Levée sans réglages mémorisés : la connexion est rétablie, aucun appel Set-CASMailbox n'est tenté.
        exo = FakeExchange()
        r = async_to_sync(self.svc(exo).release_async)(self.mb.id, confirm=self.mb.user_principal_name, by="admin")
        self.assertEqual((exo.calls, self.gw.calls[-1], r["released"]), ([], "enabled:True", True))
