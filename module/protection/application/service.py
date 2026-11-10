"""Protection automatique : réglages par organisation, évaluation périodique, réponse (alerte / désactivation)."""
from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from asgiref.sync import async_to_sync
from django.db import IntegrityError

from module.microsoft_connector.application.graph_factory import build_graph
from module.microsoft_connector.infrastructure.repositories import DjangoConnectorRepository
from module.protection.domain.rules import Limits, consent_breaches, evaluate, forwarding_breaches, rule_breaches
from module.protection.infrastructure.gateway import GraphProtectionGateway
from module.protection.infrastructure.models import ProtectionEvent, ProtectionPolicy, ProtectionState
from shared_kernel.aio import db
from shared_kernel.realtime import apublish

log = logging.getLogger(__name__)
REENABLE_WORD = "REACTIVER"
_FIELDS = ("mode", "devices_per_user_per_day", "devices_per_org_per_day", "failed_signins_per_user_per_hour",
           "disable_on_device_limit", "disable_on_signin_limit", "emails_per_user_window", "email_window_minutes",
           "disable_on_email_limit", "neutralize_rules", "neutralize_forwarding", "block_risky_consents")


class ProtectionRefused(Exception):
    pass


def limits_of(p: Optional[ProtectionPolicy]) -> Limits:
    if p is None:
        return Limits()
    return Limits(**{f: getattr(p, f) for f in _FIELDS}, exclusions=tuple(p.exclusions or ()))


def policy_dict(p: Optional[ProtectionPolicy]) -> dict:
    lim = limits_of(p)
    return {**{f: getattr(lim, f) for f in _FIELDS}, "exclusions": list(lim.exclusions),
            "updated_by": p.updated_by if p else "", "updated_at": p.modified_at if p else None, "configured": p is not None}


class ProtectionService:
    def __init__(self, connectors=None, gateway_factory: Callable[[Any], Any] = None,
                 now: Optional[Callable[[], datetime]] = None, alerts=None):
        self.connectors = connectors or DjangoConnectorRepository()
        self.gateway_factory = gateway_factory or (lambda c: GraphProtectionGateway(build_graph(c)))
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._alerts = alerts   # injectable pour les tests ; sinon AlertService (insights)

    # ------------------------------------------------------------------ réglages
    @staticmethod
    def get_policy(org_id: uuid.UUID) -> dict:
        return policy_dict(ProtectionPolicy.objects.filter(org_id=org_id).first())

    @staticmethod
    def set_policy(org_id: uuid.UUID, data: dict, by: str) -> dict:
        cur = limits_of(ProtectionPolicy.objects.filter(org_id=org_id).first())
        try:
            lim = Limits(
                mode=str(data.get("mode", cur.mode)),
                devices_per_user_per_day=int(data.get("devices_per_user_per_day", cur.devices_per_user_per_day)),
                devices_per_org_per_day=int(data.get("devices_per_org_per_day", cur.devices_per_org_per_day)),
                failed_signins_per_user_per_hour=int(data.get("failed_signins_per_user_per_hour", cur.failed_signins_per_user_per_hour)),
                disable_on_device_limit=bool(data.get("disable_on_device_limit", cur.disable_on_device_limit)),
                disable_on_signin_limit=bool(data.get("disable_on_signin_limit", cur.disable_on_signin_limit)),
                emails_per_user_window=int(data.get("emails_per_user_window", cur.emails_per_user_window)),
                email_window_minutes=int(data.get("email_window_minutes", cur.email_window_minutes)),
                disable_on_email_limit=bool(data.get("disable_on_email_limit", cur.disable_on_email_limit)),
                neutralize_rules=bool(data.get("neutralize_rules", cur.neutralize_rules)),
                neutralize_forwarding=bool(data.get("neutralize_forwarding", cur.neutralize_forwarding)),
                block_risky_consents=bool(data.get("block_risky_consents", cur.block_risky_consents)),
                exclusions=tuple(sorted({str(e).strip().lower() for e in data.get("exclusions", cur.exclusions) if str(e).strip()})),
            ).validate()
        except (TypeError, ValueError) as e:
            raise ValueError(str(e)) from e
        p, _ = ProtectionPolicy.objects.get_or_create(org_id=org_id)
        for f in _FIELDS:
            setattr(p, f, getattr(lim, f))
        p.exclusions, p.updated_by = list(lim.exclusions), by[:150]
        p.save()
        return policy_dict(p)

    # ------------------------------------------------------------------ évaluation
    def _local_sign_ins(self, connector_id) -> list[dict]:
        from module.microsoft_identity.infrastructure.sync_models import SignInRecord
        from module.microsoft_outlook.infrastructure.models import Mailbox
        boxes = {m.id: m for m in Mailbox.objects.filter(connector_id=connector_id, is_active=True)}
        since = self._now() - timedelta(hours=1)
        return [{"at": r.at, "success": bool((r.data or {}).get("success")), "ip": (r.data or {}).get("ip", ""),
                 "upn": boxes[r.mailbox_id].user_principal_name, "user_id": boxes[r.mailbox_id].graph_user_id}
                for r in SignInRecord.objects.filter(mailbox_id__in=list(boxes), at__gte=since)]

    async def evaluate_async(self, connector_id: uuid.UUID) -> dict:
        connector = await db(self.connectors.get)(connector_id)
        if connector is None or not connector.is_active:
            return {"breaches": 0}
        policy = await db(lambda: ProtectionPolicy.objects.filter(org_id=connector.org_id).first())()
        limits = limits_of(policy)
        if limits.mode == "off":
            return {"breaches": 0, "mode": "off"}
        gw = self.gateway_factory(connector)
        devices = await gw.recent_devices()
        sign_ins = await db(self._local_sign_ins)(connector_id)
        # Courriels envoyés : suivi des messages Exchange (facultatif : ignoré si Exchange n'est pas accessible).
        sent, domains, email_check = None, [], "ok"
        try:
            domains = await gw.internal_domains()
            sent = await gw.sent_messages(str(connector.tenant_id), limits.email_window_minutes)
        except Exception as e:  # noqa: BLE001
            email_check = f"indisponible ({type(e).__name__})"
            log.info("Protection : suivi des messages indisponible pour %s : %s", connector_id, e)
        breaches = evaluate(limits, devices, sign_ins, self._now(), sent=sent, internal_domains=domains)
        breaches += rule_breaches(limits, await db(self._local_rules)(connector_id), domains)
        tenant = str(connector.tenant_id)
        try:
            breaches += forwarding_breaches(limits, await gw.mailbox_forwarding(tenant), domains)
        except Exception as e:  # noqa: BLE001 : Exchange non accessible → contrôle ignoré
            log.info("Protection : transferts de boîte illisibles pour %s : %s", connector_id, e)
        try:
            apps = await gw.third_party_apps(tenant)
            known = await db(self._known_apps)(connector_id)
            breaches += consent_breaches(limits, apps, known)
            await db(self._remember_apps)(connector_id, {a["id"] for a in apps})
        except Exception as e:  # noqa: BLE001
            log.info("Protection : applications tierces illisibles pour %s : %s", connector_id, e)
        if not breaches:
            return {"breaches": 0, "mode": limits.mode, "email_check": email_check}
        # Les administrateurs ne sont JAMAIS désactivés automatiquement : relus au moment d'agir.
        try:
            admins = await gw.admin_upns() if any(b.disable for b in breaches) else set()
        except Exception:  # noqa: BLE001 : rôles illisibles → on ne désactive personne (prudence)
            admins = None
        new_events = []
        for b in breaches:
            if await db(lambda k=b.key: ProtectionEvent.objects.filter(key=k).exists())():
                continue   # déjà traité (même compte, même règle, même période)
            action, action_detail = "alerted", b.blocked_reason
            if b.disable and b.rule in NEUTRALIZE:
                try:
                    await NEUTRALIZE[b.rule](gw, tenant, b.target)
                    action, action_detail = "neutralized", NEUTRALIZED_TEXT[b.rule]
                except Exception as e:  # noqa: BLE001
                    action, action_detail = "neutralize_failed", f"Échec : {type(e).__name__}: {e}"[:500]
            elif b.disable:
                if admins is None:
                    action, action_detail = "blocked", "Rôles d'administration illisibles : désactivation annulée par prudence."
                elif b.upn.lower() in admins:
                    action, action_detail = "blocked", "Compte administrateur : jamais désactivé automatiquement."
                else:
                    try:
                        uid = b.user_id or await gw.user_id(b.upn)
                        await gw.disable_and_revoke(uid)
                        action, action_detail = "disabled", "Compte désactivé et sessions révoquées."
                        b = replace(b, user_id=uid)
                    except Exception as e:  # noqa: BLE001
                        action, action_detail = "disable_failed", f"Désactivation impossible : {type(e).__name__}: {e}"[:500]
            ev = await db(self._record)(connector, b, action, action_detail)
            if ev:
                new_events.append(ev)
        if new_events:
            await self._alert(connector, new_events)
            await apublish("protection.event", {"connector_id": str(connector_id)})
        return {"breaches": len(breaches), "new_events": len(new_events), "mode": limits.mode, "email_check": email_check}

    def evaluate(self, connector_id) -> dict:
        return async_to_sync(self.evaluate_async)(connector_id)

    def _local_rules(self, connector_id) -> list[dict]:
        from module.microsoft_outlook.infrastructure.models import OutlookRules
        return [{"id": str(r["id"]), "rule_id": r["rule_id"], "mailbox_upn": r["mailbox__user_principal_name"],
                 "display_name": r["display_name"], "is_enabled": r["is_enabled"], "actions": r["actions"]}
                for r in OutlookRules.objects.filter(mailbox__connector_id=connector_id, mailbox__is_active=True).values(
                    "id", "rule_id", "mailbox__user_principal_name", "display_name", "is_enabled", "actions")]

    @staticmethod
    def _known_apps(connector_id) -> Optional[set[str]]:
        s = ProtectionState.objects.filter(connector_id=connector_id).first()
        return None if s is None or s.known_apps is None else set(s.known_apps)

    @staticmethod
    def _remember_apps(connector_id, ids: set[str]) -> None:
        s, _ = ProtectionState.objects.get_or_create(connector_id=connector_id)
        s.known_apps = sorted(set(s.known_apps or []) | ids)
        s.save(update_fields=["known_apps", "modified_at"])

    @staticmethod
    def _record(connector, b, action, action_detail) -> Optional[dict]:
        try:
            e = ProtectionEvent.objects.create(
                connector_id=connector.id, org_id=connector.org_id, key=b.key[:300], rule=b.rule, upn=b.upn,
                user_id=b.user_id, count=b.count, limit=b.limit, detail=b.detail, items=list(b.items),
                action=action, action_detail=action_detail, target=b.target[:300])
        except IntegrityError:   # course entre deux évaluations : déjà enregistré
            return None
        return _event(e)

    async def _alert(self, connector, events: list[dict]) -> None:
        """Chaque déclenchement devient une alerte (centre d'alertes + notifications de l'organisation)."""
        from module.insights.application.alert_service import AlertService
        from module.insights.infrastructure.models import Alert
        svc = self._alerts or AlertService()

        def create():
            out = []
            for e in events:
                sev = "critical" if e["action"] == "disabled" else "high"
                verb = {"disabled": "compte DÉSACTIVÉ", "disable_failed": "désactivation ÉCHOUÉE",
                        "blocked": "compte protégé, non désactivé", "alerted": "alerte",
                        "neutralized": "NEUTRALISÉ", "neutralize_failed": "neutralisation ÉCHOUÉE"}[e["action"]]
                sev = "critical" if e["action"] in ("disabled", "neutralized") or e["rule"] in ("mailbox_forwarding", "app_consent") else sev
                a = Alert.objects.create(
                    connector_id=connector.id, org_id=connector.org_id, key=f"protection:{e['key']}"[:400],
                    category="protection", severity=sev,
                    title=f"Limite dépassée — {e['upn'] or 'tenant'} ({verb})",
                    detail=f"{e['detail']} {e['action_detail']}".strip(), link=f"/protection/{connector.org_id}",
                    data={"event_id": str(e["id"])})
                out.append({"id": a.id, "severity": sev, "category": "protection", "title": a.title, "detail": a.detail,
                            "link": a.link})
            return out
        created = await db(create)()
        try:
            await svc._send({"id": connector.id, "org_id": connector.org_id}, created)
            await apublish("alerts.updated", {"connector_id": str(connector.id)})
        except Exception:  # noqa: BLE001
            log.warning("Notification de protection non envoyée", exc_info=True)

    def evaluate_all(self) -> dict:
        total = {"evaluated": 0, "events": 0, "failed": 0}
        for c in self.connectors.list_active():
            try:
                r = self.evaluate(c.id)
                total["evaluated"] += 1
                total["events"] += r.get("new_events", 0)
            except Exception:  # noqa: BLE001
                log.exception("Protection : évaluation impossible pour %s", c.id)
                total["failed"] += 1
        return total

    # ------------------------------------------------------------------ journal et réactivation
    @staticmethod
    def events(org_id: uuid.UUID, limit: int = 100) -> list[dict]:
        return [_event(e) for e in ProtectionEvent.objects.filter(org_id=org_id)[:limit]]

    async def reenable_async(self, event_id: uuid.UUID, *, confirm: str, by: str) -> dict:
        if (confirm or "").strip().upper() != REENABLE_WORD:
            raise ProtectionRefused(f"Tapez {REENABLE_WORD} pour confirmer.")
        ev = await db(lambda: ProtectionEvent.objects.filter(pk=event_id).first())()
        if ev is None or ev.action != "disabled":
            raise ProtectionRefused("Seul un compte désactivé par la protection peut être réactivé ici.")
        if ev.reenabled_at:
            raise ProtectionRefused("Déjà réactivé.")
        connector = await db(self.connectors.get)(ev.connector_id)
        if connector is None:
            raise ProtectionRefused("Connecteur introuvable.")
        await self.gateway_factory(connector).enable(ev.user_id)

        def save():
            ev.reenabled_at, ev.reenabled_by = self._now(), by[:150]
            ev.save(update_fields=["reenabled_at", "reenabled_by", "modified_at"])
            return _event(ev)
        return await db(save)()


async def _neutralize_rule(gw, tenant, target):
    upn, rule_id = target.split("|", 1)
    await gw.disable_rule(upn, rule_id)


async def _neutralize_forwarding(gw, tenant, target):
    await gw.clear_forwarding(tenant, target)


async def _neutralize_app(gw, tenant, target):
    await gw.disable_app(target)


NEUTRALIZE = {"malicious_rule": _neutralize_rule, "mailbox_forwarding": _neutralize_forwarding, "app_consent": _neutralize_app}
NEUTRALIZED_TEXT = {
    "malicious_rule": "Règle désactivée dans Outlook (non supprimée : réactivable).",
    "mailbox_forwarding": "Transfert SMTP retiré de la boîte.",
    "app_consent": "Connexion de l'application bloquée (permissions conservées : réversible).",
}


def _event(e: ProtectionEvent) -> dict:
    return {"id": e.id, "connector_id": e.connector_id, "key": e.key, "rule": e.rule, "upn": e.upn, "user_id": e.user_id,
            "count": e.count, "limit": e.limit, "detail": e.detail, "items": e.items, "action": e.action,
            "action_detail": e.action_detail, "at": e.create_at, "target": e.target, "reenabled_at": e.reenabled_at, "reenabled_by": e.reenabled_by}
