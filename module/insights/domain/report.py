"""Agrégations pures pour les graphiques et le rapport d'organisation (aucun accès base ni réseau)."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from module.insights.domain.alerts import CATEGORY_LABEL, SEVERITIES


def _day(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def alert_trend(alerts: Iterable[dict], today: date, days: int = 30) -> list[dict]:
    """Par jour : alertes ouvertes (par gravité), résolues, et nombre d'alertes actives en fin de journée."""
    alerts = list(alerts)
    start = today - timedelta(days=days - 1)
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        opened = Counter(a["severity"] for a in alerts if _day(a["first_seen"]) == d)
        resolved = sum(1 for a in alerts if _day(a.get("resolved_at")) == d)
        active = sum(1 for a in alerts if _day(a["first_seen"]) and _day(a["first_seen"]) <= d
                     and (_day(a.get("resolved_at")) is None or _day(a.get("resolved_at")) > d))
        out.append({"day": d.isoformat(), **{s: opened.get(s, 0) for s in SEVERITIES},
                    "resolved": resolved, "active": active})
    return out


def breakdown(alerts: Iterable[dict]) -> dict:
    alerts = list(alerts)
    return {
        "by_severity": {s: sum(1 for a in alerts if a["severity"] == s) for s in SEVERITIES},
        "by_category": dict(Counter(a["category"] for a in alerts).most_common()),
    }


def mean_time_to_resolve(alerts: Iterable[dict]) -> Optional[float]:
    """Délai moyen de résolution, en heures (None si aucune alerte résolue)."""
    spans = []
    for a in alerts:
        if a.get("resolved_at") and a.get("first_seen"):
            fs, rs = a["first_seen"], a["resolved_at"]
            if isinstance(fs, str):
                fs = datetime.fromisoformat(fs.replace("Z", "+00:00"))
            if isinstance(rs, str):
                rs = datetime.fromisoformat(rs.replace("Z", "+00:00"))
            spans.append((rs - fs).total_seconds() / 3600)
    return round(sum(spans) / len(spans), 1) if spans else None


def score_delta(scores: list[float]) -> Optional[float]:
    return round(scores[-1] - scores[0], 1) if len(scores) >= 2 else None


def highlights(tenant: dict) -> list[str]:
    """Phrases de synthèse pour un tenant du rapport (les plus importantes d'abord)."""
    out = []
    a = tenant["alerts"]
    if a["open_by_severity"].get("critical"):
        out.append(f"{a['open_by_severity']['critical']} alerte(s) critique(s) encore ouverte(s).")
    if tenant.get("score") is not None:
        d = tenant.get("score_delta")
        trend = "" if d is None else (" (stable)" if d == 0 else f" ({'+' if d > 0 else ''}{d} pts sur la période)")
        out.append(f"Score de gouvernance : {round(tenant['score'])}/100{trend}.")
    s = tenant.get("sharing") or {}
    if s.get("external") or s.get("anonymous"):
        out.append(f"{s.get('external', 0)} partage(s) externe(s) et {s.get('anonymous', 0)} lien(s) anonyme(s) actifs.")
    h = tenant.get("hygiene") or {}
    if h.get("apps_high"):
        out.append(f"{h['apps_high']} application(s) tierce(s) à haut risque autorisée(s).")
    if h.get("inactive"):
        out.append(f"{h['inactive']} compte(s) inactif(s) depuis plus de 90 jours.")
    if a["resolved"]:
        out.append(f"{a['resolved']} alerte(s) résolue(s) pendant la période.")
    if tenant.get("actions"):
        out.append(f"{len(tenant['actions'])} action(s) de remédiation effectuée(s) depuis OverView.")
    return out or ["Aucun point d'attention sur la période."]


__all__ = ["alert_trend", "breakdown", "mean_time_to_resolve", "score_delta", "highlights", "CATEGORY_LABEL"]
