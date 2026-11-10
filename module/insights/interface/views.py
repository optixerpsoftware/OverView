"""Vues async : tableau de bord, alertes, notifications, recherche, actions en lot."""
from __future__ import annotations

import uuid

from module.insights.application.alert_service import AlertService
from module.insights.application.services import BulkService, DashboardService, InvalidBulk, ReportService, SearchService
from module.insights.domain.alerts import SEVERITIES
from module.insights.infrastructure.models import BulkActionLog, NotificationSetting
from module.insights.infrastructure.notifier import valid_teams_webhook
from shared_kernel.aio import db
from shared_kernel.async_api import AsyncAPIView, Response, status


class _View(AsyncAPIView):
    def handle_exception(self, exc):
        if isinstance(exc, (InvalidBulk, ValueError)):
            return Response({"detail": str(exc)}, status.HTTP_400_BAD_REQUEST)
        return super().handle_exception(exc)


def _uuid(value):
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError as e:
        raise ValueError("Identifiant invalide.") from e


class DashboardView(_View):
    async def get(self, request):
        return Response(await DashboardService().build_async())


class AlertListView(_View):
    """GET ?status=active|open|acknowledged|resolved|all&connector_id=&org_id=&severity=&category="""

    async def get(self, request):
        q = request.query_params
        rows = await db(AlertService.list)(
            status=q.get("status", "active"), connector_id=_uuid(q.get("connector_id")), org_id=_uuid(q.get("org_id")),
            severity=q.get("severity", ""), category=q.get("category", ""))
        return Response(rows)


class AlertCountsView(_View):
    async def get(self, request):
        return Response(await db(AlertService.counts)(_uuid(request.query_params.get("org_id"))))


class AlertStatusView(_View):
    """POST {ids: [...], status: "acknowledged"|"open"} : prendre en compte / rouvrir (en lot possible)."""

    async def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        ids = [_uuid(i) for i in (body.get("ids") or [])][:500]
        n = await db(AlertService.set_status)(ids, str(body.get("status", "")), request.user.get_username())
        return Response({"updated": n})


class AlertEvaluateView(_View):
    """POST {connector_id} : réévalue tout de suite les alertes d'un tenant (sinon : toutes les 10 min)."""

    async def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        cid = _uuid(body.get("connector_id"))
        if not await db(_connector_in_scope)(cid):
            return Response({"detail": "Introuvable."}, status.HTTP_404_NOT_FOUND)
        return Response(await AlertService().evaluate_async(cid))


def _connector_in_scope(connector_id) -> bool:
    from module.microsoft_connector.infrastructure.models import MicrosoftConnector
    from shared_kernel.scope import scope_filter
    return bool(connector_id) and scope_filter(MicrosoftConnector.objects.filter(pk=connector_id)).exists()


def _rules_in_scope(rule_ids) -> bool:
    from module.microsoft_outlook.infrastructure.models import OutlookRules
    from shared_kernel.scope import allowed_orgs, scope_filter
    if allowed_orgs() is None:
        return True
    ids = [_uuid(i) for i in rule_ids]
    return scope_filter(OutlookRules.objects.filter(pk__in=ids), "mailbox__org_id").count() == len(set(ids))


class NotificationSettingView(_View):
    """GET / PUT la configuration des notifications d'une organisation. Le webhook n'est jamais renvoyé en clair."""

    async def get(self, request, org_id):
        s = await db(lambda: NotificationSetting.objects.filter(org_id=org_id).first())()
        return Response(_setting(s))

    async def put(self, request, org_id):
        body = request.data if isinstance(request.data, dict) else {}
        emails = [str(e).strip() for e in (body.get("emails") or []) if str(e).strip()][:20]
        if any("@" not in e or " " in e for e in emails):
            raise ValueError("Adresse courriel invalide.")
        sev = str(body.get("min_severity", "high"))
        if sev not in SEVERITIES:
            raise ValueError("Gravité invalide.")
        webhook = body.get("teams_webhook")   # None = inchangé ; "" = supprimer
        if webhook and not valid_teams_webhook(str(webhook)):
            raise ValueError("Le webhook doit être une adresse https de Microsoft Teams / Power Automate.")

        def save():
            s, _ = NotificationSetting.objects.get_or_create(org_id=org_id)
            s.emails, s.min_severity, s.enabled = emails, sev, bool(body.get("enabled", True))
            if webhook is not None:
                s.teams_webhook = str(webhook)
            s.save()
            return s
        return Response(_setting(await db(save)()))


def _setting(s) -> dict:
    if s is None:
        return {"emails": [], "min_severity": "high", "enabled": True, "teams_configured": False}
    return {"emails": s.emails, "min_severity": s.min_severity, "enabled": s.enabled,
            "teams_configured": bool(s.teams_webhook)}


class SearchView(_View):
    async def get(self, request):
        return Response(await SearchService().search_async(request.query_params.get("q", "")[:100]))


class BulkRulesDeleteView(_View):
    """POST {rule_ids: [...], confirm: "SUPPRIMER"} : supprime les règles dans Outlook (journalisé)."""

    async def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        if not await db(_rules_in_scope)(list(body.get("rule_ids") or [])):
            return Response({"detail": "Règle hors de tes organisations."}, status.HTTP_404_NOT_FOUND)
        return Response(await BulkService().delete_rules_async(
            list(body.get("rule_ids") or []), confirm=str(body.get("confirm", "")), by=request.user.get_username()))


class BulkSharingRevokeView(_View):
    """POST {connector_id, items: [{drive_id, item_id, permission_id}], confirm: "REVOQUER"}."""

    async def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        items = [i for i in (body.get("items") or []) if isinstance(i, dict)]
        if not await db(_connector_in_scope)(_uuid(body.get("connector_id"))):
            return Response({"detail": "Introuvable."}, status.HTTP_404_NOT_FOUND)
        return Response(await BulkService().revoke_shares_async(
            _uuid(body.get("connector_id")), items, confirm=str(body.get("confirm", "")), by=request.user.get_username()))


class BulkLogView(_View):
    async def get(self, request):
        from shared_kernel.scope import allowed_orgs
        if allowed_orgs() is not None:          # journal global : réservé aux comptes qui voient tous les clients
            return Response([])
        rows = await db(lambda: list(BulkActionLog.objects.values(
            "id", "action", "performed_by", "total", "succeeded", "results", "create_at")[:50]))()
        return Response(rows)



class OrganizationReportView(_View):
    """GET ?days=7|30|90 : rapport de l'organisation (score, alertes, partages, identités, remédiations)."""

    async def get(self, request, org_id):
        days = request.query_params.get("days", "30")
        data = await ReportService().build_async(org_id, int(days) if days.isdigit() else 30)
        if data is None:
            return Response({"detail": "Organisation introuvable."}, status.HTTP_404_NOT_FOUND)
        return Response(data)



# ---------------------------------------------------------------- file de travail « À faire »
from module.insights.application.queue_service import QueueService  # noqa: E402


def _ids(body) -> list:
    return [_uuid(i) for i in (body.get("ids") or [])][:500]


class QueueView(_View):
    """GET ?scope=all|mine|unassigned&org_id=&snoozed=1 : alertes à traiter avec leurs actions + compteurs."""

    async def get(self, request):
        q, me = request.query_params, request.user.get_username()
        items = await db(QueueService.items)(me, scope=q.get("scope", "all"), org_id=_uuid(q.get("org_id")),
                                             include_snoozed=q.get("snoozed") == "1")
        return Response({"items": items, "stats": await db(QueueService.stats)(me), "me": me})


class QueueAssignView(_View):
    """POST {ids, to: "me" | "<utilisateur>" | null}."""

    async def post(self, request):
        b = request.data if isinstance(request.data, dict) else {}
        to = request.user.get_username() if b.get("to") == "me" else b.get("to")
        return Response({"updated": await db(QueueService.assign)(_ids(b), to)})


class QueueSnoozeView(_View):
    """POST {ids, hours: 1|4|24|72|168} : reporter ; {ids, hours: 0} : reprendre tout de suite."""

    async def post(self, request):
        b = request.data if isinstance(request.data, dict) else {}
        hours = int(b.get("hours", 0) or 0)
        n = await db(QueueService.unsnooze)(_ids(b)) if hours == 0 else await db(QueueService.snooze)(_ids(b), hours)
        return Response({"updated": n})


class QueueNoteView(_View):
    """POST {text} : ajoute une note horodatée à l'alerte (suivi d'équipe)."""

    async def post(self, request, alert_id):
        b = request.data if isinstance(request.data, dict) else {}
        return Response(await db(QueueService.set_note)(alert_id, str(b.get("text", "")), request.user.get_username()))
