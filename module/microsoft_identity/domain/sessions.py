"""Estimation des sessions actives.

Microsoft Graph n'expose AUCUNE liste de sessions actives. On l'estime ainsi : un jeton
d'actualisation vit au plus 90 jours (et reste valable tant qu'il n'est pas révoqué), donc
toute connexion réussie postérieure à la dernière révocation (`signInSessionsValidFrom`)
et récente (journal Entra : ~30 jours) peut correspondre à une session encore utilisable.
Les connexions sont regroupées par application + IP + appareil.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from module.microsoft_identity.domain.entities import ActiveSession, SignInInfo

SESSION_LIFETIME = timedelta(days=90)


def estimate_active_sessions(
    sign_ins: Iterable[SignInInfo],
    sessions_valid_from: Optional[datetime],
    now: Optional[datetime] = None,
) -> list[ActiveSession]:
    now = now or datetime.now(timezone.utc)
    groups: dict[tuple, list[SignInInfo]] = {}
    for s in sign_ins:
        if not s.success or s.at is None:
            continue
        if now - s.at > SESSION_LIFETIME:
            continue
        if sessions_valid_from is not None and s.at <= sessions_valid_from:
            continue  # révoquée depuis
        groups.setdefault((s.app, s.ip, s.os, s.browser), []).append(s)

    out = []
    for items in groups.values():
        items.sort(key=lambda x: x.at)
        first, last = items[0], items[-1]
        out.append(ActiveSession(
            app=last.app, client=last.client, ip=last.ip, city=last.city, country=last.country,
            os=last.os, browser=last.browser, first_seen=first.at, last_seen=last.at,
            count=len(items), interactive=any(i.interactive for i in items),
        ))
    out.sort(key=lambda x: x.last_seen, reverse=True)
    return out
