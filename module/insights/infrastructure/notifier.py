"""Envoi des notifications d'alertes : courriel (backend Django configuré) et webhook Microsoft Teams."""
from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx
from django.core.mail import send_mail

from module.insights.domain.alerts import CATEGORY_LABEL, SEVERITY_LABEL

log = logging.getLogger(__name__)

# Le webhook doit pointer vers Microsoft (Teams / Power Automate) : jamais vers une adresse arbitraire du réseau.
_TEAMS_HOST_SUFFIXES = (".webhook.office.com", ".logic.azure.com", ".powerplatform.com", ".powerautomate.com")


def valid_teams_webhook(url: str) -> bool:
    if not url:
        return True
    u = urlparse(url)
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host.endswith(s) for s in _TEAMS_HOST_SUFFIXES)


def _lines(alerts: list[dict], base_url: str) -> list[str]:
    return [f"[{SEVERITY_LABEL.get(a['severity'], a['severity'])}] {CATEGORY_LABEL.get(a['category'], a['category'])} — "
            f"{a['title']}\n    {a['detail']}\n    {base_url}{a['link']}" for a in alerts]


def send_email(recipients: list[str], org_name: str, alerts: list[dict], base_url: str) -> None:
    subject = f"[OverView] {len(alerts)} nouvelle(s) alerte(s) — {org_name}"
    body = f"Nouvelles alertes pour {org_name} :\n\n" + "\n\n".join(_lines(alerts, base_url))
    send_mail(subject, body, None, recipients, fail_silently=False)


def send_teams(webhook: str, org_name: str, alerts: list[dict], base_url: str) -> None:
    if not valid_teams_webhook(webhook):
        raise ValueError("Webhook Teams invalide.")
    facts = [{
        "type": "TextBlock", "wrap": True, "spacing": "Medium",
        "text": f"**{SEVERITY_LABEL.get(a['severity'], a['severity'])}** · {a['title']}\n\n{a['detail']}"
                + (f"\n\n[Ouvrir]({base_url}{a['link']})" if base_url else ""),
    } for a in alerts[:15]]
    card = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4",
                "body": [{"type": "TextBlock", "size": "Medium", "weight": "Bolder",
                          "text": f"OverView — {len(alerts)} nouvelle(s) alerte(s) · {org_name}"}] + facts,
            },
        }],
    }
    r = httpx.post(webhook, json=card, timeout=20)
    r.raise_for_status()
