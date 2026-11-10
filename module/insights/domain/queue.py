"""File de travail : pour chaque alerte, les actions qui la règlent sur place. Fonction pure (aucun accès réseau).

Chaque action décrit l'appel d'API à faire (méthode, chemin, corps) et la confirmation à demander. Le frontend
n'invente rien : il affiche ces actions telles quelles, et chaque appel repasse par les garde-fous du serveur.
"""
from __future__ import annotations

import re
from typing import Optional

_MAILBOX = re.compile(r"^/mailboxes/([0-9a-f-]{36})")


def _upn_from_title(title: str) -> Optional[str]:
    tail = title.rsplit(" : ", 1)[-1].strip()
    return tail if "@" in tail and " " not in tail else None


def quick_actions(alert: dict) -> list[dict]:
    """Actions proposées pour une alerte ouverte. `alert` : dict d'Alert (category, key, data, link, title…)."""
    cat, data, link = alert["category"], alert.get("data") or {}, alert.get("link") or ""
    cid = str(alert["connector_id"])
    out: list[dict] = []

    if cat == "sharing" and {"drive_id", "item_id", "permission_id"} <= set(data):
        out.append({"kind": "revoke_share", "label": "Révoquer le partage", "danger": True, "method": "DELETE",
                    "path": f"/onedrive/connectors/{cid}/drives/{data['drive_id']}/items/{data['item_id']}/permissions/{data['permission_id']}/",
                    "body": None, "confirm": "Le lien ou le partage sera supprimé dans Microsoft 365. Irréversible.",
                    "resolves": True})
        if data.get("web_url"):
            out.append({"kind": "open_external", "label": "Ouvrir le fichier", "href": data["web_url"]})

    elif cat == "rule" and data.get("rule_id"):
        out.append({"kind": "delete_rule", "label": "Supprimer la règle", "danger": True, "method": "DELETE",
                    "path": f"/outlook/rules/{data['rule_id']}/", "body": None,
                    "confirm": "La règle sera supprimée dans Outlook. Irréversible.", "resolves": True})

    elif cat == "app" and data.get("sp_id"):
        out.append({"kind": "block_app", "label": "Bloquer l'application", "danger": True, "method": "POST",
                    "path": f"/identity/connectors/{cid}/hygiene/apps/{data['sp_id']}/",
                    "body": {"enabled": False, "confirm": "DESACTIVER"},
                    "confirm": "Plus personne ne pourra se connecter à cette application (réversible).", "resolves": True})

    m = _MAILBOX.match(link)
    upn = _upn_from_title(alert.get("title", ""))
    if m and upn and cat in ("identity", "signin"):
        mb = m.group(1)
        if alert.get("severity") in ("critical", "high"):
            out.append({"kind": "quarantine", "label": "Mettre en quarantaine", "danger": True, "method": "POST",
                        "path": f"/identity/mailboxes/{mb}/quarantine/",
                        "body": {"confirm": upn, "reason": alert.get("title", "")[:200]},
                        "confirm": f"{upn} ne pourra plus se connecter ; la boîte continue de recevoir (réversible).",
                        "require_text": upn, "resolves": False})
        out.append({"kind": "revoke_sessions", "label": "Déconnecter partout", "danger": False, "method": "POST",
                    "path": f"/identity/mailboxes/{mb}/revoke-sessions/", "body": {},
                    "confirm": f"{upn} sera déconnecté de toutes ses sessions.", "resolves": False})

    if link:
        out.append({"kind": "open", "label": "Ouvrir", "href": link})
    return out
