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
        return Response(await AlertService().evaluate_async(_uuid(body.get("connector_id"))))


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
        return Response(await BulkService().delete_rules_async(
            list(body.get("rule_ids") or []), confirm=str(body.get("confirm", "")), by=request.user.get_username()))


class BulkSharingRevokeView(_View):
    """POST {connector_id, items: [{drive_id, item_id, permission_id}], confirm: "REVOQUER"}."""

    async def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        items = [i for i in (body.get("items") or []) if isinstance(i, dict)]
        return Response(await BulkService().revoke_shares_async(
            _uuid(body.get("connector_id")), items, confirm=str(body.get("confirm", "")), by=request.user.get_username()))


class BulkLogView(_View):
    async def get(self, request):
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
