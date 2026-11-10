from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from asgiref.sync import async_to_sync
from django.utils import timezone as dj_timezone

from module.microsoft_connector.application.graph_factory import build_graph
from module.microsoft_connector.domain.repositories import ConnectorRepository
from module.microsoft_connector.infrastructure.repositories import DjangoConnectorRepository
from module.microsoft_identity.application.services import ConnectorUnavailable, MailboxNotFound
from module.microsoft_identity.domain.entities import Section
from module.microsoft_identity.domain.ports import AuditLog, IdentityGateway, SecuritySyncRepository
from module.microsoft_identity.domain.sessions import estimate_active_sessions
from module.microsoft_identity.infrastructure.audit import DjangoAuditLog
from module.microsoft_identity.infrastructure.graph_gateway import GraphIdentityGateway
from module.microsoft_identity.infrastructure.sync_repo import DjangoSecuritySyncRepository
from module.microsoft_outlook.domain.repositories import MailboxRepository
from module.microsoft_outlook.infrastructure.repositories import DjangoMailboxRepository
from shared_kernel.realtime import publish

SIGNIN_FIRST_DAYS = 30       # première synchro : 30 jours (rétention des journaux Microsoft)
SIGNIN_OVERLAP = timedelta(minutes=10)   # chevauchement : les journaux Microsoft arrivent parfois avec du retard
SIGNIN_KEEP_DAYS = 90        # on conserve l'historique localement plus longtemps que Microsoft


def celery_enqueue(mailbox_id: str) -> None:
    """Met la synchro en file (worker Celery). Import tardif : l'interface dépend de l'application, pas l'inverse."""
    from module.microsoft_identity.interface.tasks import sync_mailbox_security
    sync_mailbox_security.delay(mailbox_id)


def _default_gateway(connector) -> IdentityGateway:
    return GraphIdentityGateway(build_graph(connector))


def _jsonable(obj):
    """Dataclasses / datetimes -> structures JSON (pour le stockage de l'instantané)."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return _jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def _parse_dt(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _err(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:400]


def sync_info(snap: Optional[dict]) -> dict:
    s = snap or {}
    return {"status": s.get("status", "idle"), "last_synced_at": s.get("last_synced_at"),
            "last_error": s.get("last_error", "")}


class SecuritySyncService:
    """Synchronisation en arrière-plan de la sécurité d'un compte (profil, appareils, MFA, connexions).

    Les pages lisent l'instantané local (instantané) ; un worker le rafraîchit toutes les 10 minutes et après
    chaque action. Les connexions sont collectées de façon incrémentale : seulement celles créées depuis la
    plus récente déjà stockée (plus un court chevauchement dédupliqué)."""

    def __init__(
        self,
        mailboxes: Optional[MailboxRepository] = None,
        connectors: Optional[ConnectorRepository] = None,
        repo: Optional[SecuritySyncRepository] = None,
        audit: Optional[AuditLog] = None,
        gateway_factory: Optional[Callable] = None,
        enqueue: Optional[Callable[[str], None]] = None,
    ):
        self.mailboxes = mailboxes or DjangoMailboxRepository()
        self.connectors = connectors or DjangoConnectorRepository()
        self.repo = repo or DjangoSecuritySyncRepository()
        self.audit = audit or DjangoAuditLog()
        self.gateway_factory = gateway_factory or _default_gateway     # résolus à l'exécution (substituables en test)
        self.enqueue = enqueue or celery_enqueue

    def _mailbox(self, mailbox_id: uuid.UUID):
        mailbox = self.mailboxes.get(mailbox_id)
        if mailbox is None:
            raise MailboxNotFound(str(mailbox_id))
        return mailbox

    def _connector(self, mailbox):
        c = self.connectors.get(mailbox.connector_id)
        if c is None or not c.is_active:
            raise ConnectorUnavailable(str(mailbox.connector_id))
        return c

    # ---------- lecture (locale, instantanée) ----------
    def overview(self, mailbox_id: uuid.UUID) -> dict:
        mailbox = self._mailbox(mailbox_id)
        snap = self.repo.get(mailbox.id)
        if snap is None or (snap["last_synced_at"] is None and snap["status"] != "running"):
            self.request_sync(mailbox.id)           # première ouverture : on lance la synchro, la page attend
            snap = self.repo.get(mailbox.id)
        return {
            "mailbox": {"id": mailbox.id, "user_principal_name": mailbox.user_principal_name},
            "profile": (snap or {}).get("profile"),
            "devices": Section(items=(snap or {}).get("devices", []), error=(snap or {}).get("devices_error", "")),
            "auth_methods": Section(items=(snap or {}).get("auth_methods", []),
                                    error=(snap or {}).get("auth_methods_error", "")),
            "actions": self.audit.recent(mailbox.id),
            "sync": sync_info(snap),
        }

    def activity(self, mailbox_id: uuid.UUID) -> dict:
        mailbox = self._mailbox(mailbox_id)
        snap = self.repo.get(mailbox.id) or {}
        sign_ins = self.repo.list_sign_ins(mailbox.id, limit=500)
        err = snap.get("sign_ins_error", "")
        valid_from = _parse_dt((snap.get("profile") or {}).get("sessions_valid_from"))
        return {
            "sign_ins": Section(items=sign_ins[:100], error=err),
            "active_sessions": (Section(error=err) if err and not sign_ins
                                else Section(items=estimate_active_sessions(sign_ins, valid_from))),
            "sync": sync_info(snap),
        }

    # ---------- synchro ----------
    def request_sync(self, mailbox_id: uuid.UUID) -> bool:
        """Planifie une synchro asynchrone. Faux si une synchro est déjà en cours pour ce compte."""
        mailbox = self._mailbox(mailbox_id)
        self._connector(mailbox)
        if not self.repo.try_start(mailbox.id, mailbox.org_id):
            return False
        try:
            self.enqueue(str(mailbox.id))
        except Exception as e:  # broker (Redis) indisponible : on ne laisse pas la synchro « en cours » à tort
            self.repo.finish(mailbox.id, f"File de tâches indisponible : {_err(e)}")
            self._notify(mailbox.id)
            raise
        self._notify(mailbox.id)
        return True

    def _notify(self, mailbox_id: uuid.UUID) -> None:
        """Prévient les navigateurs connectés (WebSocket) que l'état de synchro de ce compte a changé."""
        info = sync_info(self.repo.get(mailbox_id))
        publish("security.sync", {"mailbox_id": str(mailbox_id), "status": info["status"],
                                  "last_synced_at": info["last_synced_at"].isoformat() if info["last_synced_at"] else None})

    def sync_mailbox(self, mailbox_id: uuid.UUID) -> dict:
        """Synchro immédiate et bloquante (CLI, tests) : prend le verrou puis exécute."""
        mailbox = self._mailbox(mailbox_id)
        self._connector(mailbox)
        if not self.repo.try_start(mailbox.id, mailbox.org_id):
            return {"skipped": "synchronisation déjà en cours"}
        return self.run_sync(mailbox_id)

    def run_sync(self, mailbox_id: uuid.UUID) -> dict:
        """Exécute la synchro (le verrou « en cours » est déjà pris par `request_sync`/`sync_mailbox`)."""
        mailbox = self._mailbox(mailbox_id)
        try:
            connector = self._connector(mailbox)
            gw = self.gateway_factory(connector)
            upn = mailbox.user_principal_name
            cursor = self.repo.latest_sign_in_at(mailbox.id)
            since = (cursor - SIGNIN_OVERLAP) if cursor else (datetime.now(timezone.utc) - timedelta(days=SIGNIN_FIRST_DAYS))

            async def collect():
                devices_t = asyncio.ensure_future(gw.list_devices(upn))
                methods_t = asyncio.ensure_future(gw.list_auth_methods(upn))
                profile = await gw.get_profile(upn)     # sans profil on s'arrête : l'erreur remonte
                try:
                    sign_ins = await gw.list_sign_ins(profile.user_id, since=since)
                except Exception as e:
                    sign_ins = e
                devices, methods = await asyncio.gather(devices_t, methods_t, return_exceptions=True)
                return profile, devices, methods, sign_ins

            profile, devices, methods, sign_ins = async_to_sync(collect)()
        except Exception as e:
            self.repo.finish(mailbox.id, _err(e))
            self._notify(mailbox.id)
            raise

        old = self.repo.get(mailbox.id) or {}
        if not isinstance(methods, BaseException):
            self._record_method_changes(mailbox, old, _jsonable(methods))
        # Une section en erreur conserve ses dernières données connues (et affiche l'erreur).
        self.repo.save_snapshot(
            mailbox.id, profile=_jsonable(profile),
            devices=old.get("devices", []) if isinstance(devices, BaseException) else _jsonable(devices),
            devices_error=_err(devices) if isinstance(devices, BaseException) else "",
            auth_methods=old.get("auth_methods", []) if isinstance(methods, BaseException) else _jsonable(methods),
            auth_methods_error=_err(methods) if isinstance(methods, BaseException) else "",
            sign_ins_error=_err(sign_ins) if isinstance(sign_ins, BaseException) else "",
        )
        added = 0
        if not isinstance(sign_ins, BaseException):
            added = self.repo.add_sign_ins(mailbox.id, mailbox.org_id, sign_ins)
        self.repo.prune_sign_ins(mailbox.id, dj_timezone.now() - timedelta(days=SIGNIN_KEEP_DAYS))
        self.repo.finish(mailbox.id)
        self._notify(mailbox.id)
        return {"new_sign_ins": added, "since": since.isoformat()}

    @staticmethod
    def _record_method_changes(mailbox, old: dict, new_methods: list) -> None:
        """Note les méthodes MFA ajoutées / retirées depuis la synchro précédente. Rien à la toute première synchro
        (pas de référence) ni si la lecture précédente était en erreur (on comparerait à une liste incomplète)."""
        from module.microsoft_identity.infrastructure.sync_models import AuthMethodChange
        if not old or old.get("auth_methods_error") or not old.get("last_synced_at"):
            return
        before = {m.get("id"): m for m in old.get("auth_methods") or []}
        after = {m.get("id"): m for m in new_methods or []}
        now = dj_timezone.now()
        rows = [AuthMethodChange(mailbox_id=mailbox.id, org_id=mailbox.org_id, method_id=str(i)[:128], kind=m.get("kind", "")[:40],
                                 label=(m.get("label") or "")[:200], detail=(m.get("detail") or "")[:300], change=change, at=now)
                for change, ids, src in (("added", after.keys() - before.keys(), after), ("removed", before.keys() - after.keys(), before))
                for i in ids for m in [src[i]] if m.get("kind") != "password"]
        if rows:
            AuthMethodChange.objects.bulk_create(rows)

    def sync_all(self) -> dict:
        """Planifie la synchro de tous les comptes actifs (appelé toutes les 10 minutes par le worker).
        La concurrence du worker limite le nombre d'appels Graph simultanés."""
        queued = skipped = 0
        for mb in self.mailboxes.list_active():
            try:
                if self.request_sync(mb.id):
                    queued += 1
                else:
                    skipped += 1           # déjà en cours
            except Exception:
                skipped += 1               # connecteur inactif ou file indisponible
        return {"queued": queued, "skipped": skipped}
