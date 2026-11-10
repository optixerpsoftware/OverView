"""Règles d'alerte : fonctions pures qui transforment l'état connu des tenants en alertes candidates.

Chaque alerte a une clé stable (`key`) : la même situation produit toujours la même clé, ce qui permet de ne pas
recréer (ni re-notifier) une alerte déjà ouverte, et de la clore automatiquement quand la situation disparaît.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional

SEVERITIES = ("critical", "high", "medium", "low")
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITIES)}
SEVERITY_LABEL = {"critical": "Critique", "high": "Élevée", "medium": "Moyenne", "low": "Faible"}

CATEGORY_LABEL = {
    "sharing": "Partage", "rule": "Règle Outlook", "identity": "Compte", "signin": "Connexion",
    "governance": "Gouvernance", "quarantine": "Quarantaine", "app": "Application tierce", "protection": "Protection automatique",
}

FORWARD_ACTIONS = ("forwardTo", "forwardAsAttachmentTo", "redirectTo")
FAILED_SIGNIN_THRESHOLD = 10


@dataclass(frozen=True)
class AlertCandidate:
    key: str
    category: str
    severity: str
    title: str
    detail: str
    link: str = ""
    data: dict = field(default_factory=dict)


def at_least(severity: str, minimum: str) -> bool:
    return SEVERITY_RANK.get(severity, 99) <= SEVERITY_RANK.get(minimum, 99)


# ---------------------------------------------------------------- partage OneDrive / SharePoint
def sharing_alerts(connector_id: str, records: Iterable[dict]) -> list[AlertCandidate]:
    """Liens anonymes et partages externes (la priorité de l'administrateur : ce qui sort de l'organisation)."""
    out = []
    for r in records:
        scope, external = r.get("scope"), bool(r.get("external"))
        if scope != "anonymous" and not external:
            continue
        edit = r.get("link_type") == "edit" or "write" in (r.get("roles") or [])
        if scope == "anonymous":
            sev = "critical" if edit else "high"
            title = f"Lien anonyme {'modifiable' if edit else 'en lecture'} : {r.get('name', '?')}"
        else:
            sev = "high" if edit else "medium"
            title = f"Partagé à l'extérieur {'(modification)' if edit else '(lecture)'} : {r.get('name', '?')}"
        who = ", ".join(r.get("grantees") or []) or "toute personne disposant du lien"
        out.append(AlertCandidate(
            key=f"sharing:{r.get('drive_id')}:{r.get('item_id')}:{r.get('permission_id')}",
            category="sharing", severity=sev, title=title,
            detail=f"{r.get('drive_label', '')}{r.get('path', '')} — {who}",
            link=f"/sharing/{connector_id}",
            data={k: r.get(k) for k in ("drive_id", "item_id", "permission_id", "web_url", "drive_label", "path")},
        ))
    return out


# ---------------------------------------------------------------- règles Outlook
def _addresses(value) -> list[str]:
    out = []
    for a in value or []:
        e = (a or {}).get("emailAddress") or {}
        if e.get("address"):
            out.append(e["address"].lower())
    return out


def rule_alerts(rules: Iterable[dict], internal_domains: Iterable[str]) -> list[AlertCandidate]:
    """Règles qui envoient le courrier hors de l'organisation ou le font disparaître (signes de compromission)."""
    internal = {d.lower() for d in internal_domains}
    out = []
    for r in rules:
        actions = r.get("actions") or {}
        if not r.get("is_enabled", True):
            continue
        targets = [a for k in FORWARD_ACTIONS for a in _addresses(actions.get(k))]
        external = [a for a in targets if a.rsplit("@", 1)[-1] not in internal]
        mailbox = r.get("mailbox_upn", "")
        link = f"/mailboxes/{r.get('mailbox_id')}"
        if external:
            out.append(AlertCandidate(
                key=f"rule:{r.get('id')}:forward", category="rule", severity="high",
                title=f"Transfert automatique vers l'extérieur : {mailbox}",
                detail=f"Règle « {r.get('display_name', '')} » → {', '.join(sorted(set(external)))}",
                link=link, data={"rule_id": str(r.get("id")), "mailbox_id": str(r.get("mailbox_id"))},
            ))
        hides = actions.get("permanentDelete") or (actions.get("delete") and actions.get("markAsRead"))
        if hides:
            out.append(AlertCandidate(
                key=f"rule:{r.get('id')}:hide", category="rule", severity="medium",
                title=f"Règle qui fait disparaître des messages : {mailbox}",
                detail=f"Règle « {r.get('display_name', '')} » : "
                       + ("suppression définitive" if actions.get("permanentDelete") else "suppression + marqué comme lu"),
                link=link, data={"rule_id": str(r.get("id")), "mailbox_id": str(r.get("mailbox_id"))},
            ))
    return out


# ---------------------------------------------------------------- comptes et connexions
def identity_alerts(mailbox: dict, auth_methods: list[dict], profile: Optional[dict]) -> list[AlertCandidate]:
    if profile is not None and profile.get("account_enabled") is False:
        return []
    if not auth_methods or any(m.get("is_mfa") for m in auth_methods):
        return []   # méthodes inconnues (pas encore synchronisées) : pas d'alerte à l'aveugle
    return [AlertCandidate(
        key=f"identity:{mailbox['id']}:no-mfa", category="identity", severity="high",
        title=f"Compte sans MFA : {mailbox['upn']}",
        detail="Seul le mot de passe protège ce compte. Ajoute une méthode MFA.",
        link=f"/mailboxes/{mailbox['id']}",
    )]


def signin_alerts(mailbox: dict, sign_ins: list[dict], now: datetime, window: timedelta = timedelta(hours=24),
                  history: timedelta = timedelta(days=30)) -> list[AlertCandidate]:
    """`sign_ins` : dicts {at: datetime, success, country, ip}. Nouveau pays et rafale d'échecs sur 24 h."""
    recent = [s for s in sign_ins if s["at"] >= now - window]
    older = [s for s in sign_ins if now - history <= s["at"] < now - window and s.get("success")]
    out = []
    known = {s.get("country") for s in older if s.get("country")}
    if known:   # sans historique, tout pays serait « nouveau »
        new = sorted({s["country"] for s in recent if s.get("success") and s.get("country") and s["country"] not in known})
        for c in new:
            ips = sorted({s.get("ip", "") for s in recent if s.get("country") == c})
            out.append(AlertCandidate(
                key=f"signin:{mailbox['id']}:country:{c}:{now.date().isoformat()}", category="signin", severity="high",
                title=f"Connexion réussie depuis un nouveau pays ({c}) : {mailbox['upn']}",
                detail=f"Pays habituels : {', '.join(sorted(known))}. Adresse(s) IP : {', '.join(ips)}",
                link=f"/mailboxes/{mailbox['id']}",
            ))
    failed = [s for s in recent if not s.get("success")]
    if len(failed) >= FAILED_SIGNIN_THRESHOLD:
        top = Counter(s.get("ip", "?") for s in failed).most_common(3)
        out.append(AlertCandidate(
            key=f"signin:{mailbox['id']}:failures:{now.date().isoformat()}", category="signin", severity="medium",
            title=f"{len(failed)} échecs de connexion en 24 h : {mailbox['upn']}",
            detail="Principales IP : " + ", ".join(f"{ip} ({n})" for ip, n in top),
            link=f"/mailboxes/{mailbox['id']}",
        ))
    return out


# ---------------------------------------------------------------- gouvernance
def governance_alerts(connector_id: str, findings: Iterable[dict]) -> list[AlertCandidate]:
    out = []
    for f in findings:
        if f.get("status") != "fail" or f.get("severity") not in ("critical", "high"):
            continue
        out.append(AlertCandidate(
            key=f"governance:{connector_id}:{f.get('code')}", category="governance", severity=f["severity"],
            title=f.get("title", f.get("code", "?")), detail=f.get("summary", ""),
            link=f"/compliance/{connector_id}",
        ))
    return out


# ---------------------------------------------------------------- quarantaine
def quarantine_alerts(connector_id: str, messages: Iterable[dict]) -> list[AlertCandidate]:
    """`messages` : dicts {identity, type, subject, sender, recipients, released}. Malveillant / hameçonnage sûr."""
    out = []
    for m in messages:
        if m.get("released") or m.get("type") not in ("Malware", "HighConfPhish"):
            continue
        kind = "logiciel malveillant" if m["type"] == "Malware" else "hameçonnage (haute confiance)"
        out.append(AlertCandidate(
            key=f"quarantine:{m.get('identity')}", category="quarantine", severity="high",
            title=f"Message bloqué ({kind}) : {m.get('subject') or '(sans objet)'}",
            detail=f"De {m.get('sender', '?')} vers {', '.join(m.get('recipients') or []) or '?'}",
            link=f"/mailflow/{connector_id}",
        ))
    return out


# ---------------------------------------------------------------- hygiène des identités
def hygiene_alerts(connector_id: str, snapshot: dict) -> list[AlertCandidate]:
    """Applications tierces à haut risque (accès au courrier / aux fichiers de toute l'organisation) et admins sans MFA."""
    out = []
    for a in snapshot.get("apps") or []:
        if a.get("risk") != "high" or not a.get("enabled", True):
            continue
        out.append(AlertCandidate(
            key=f"app:{connector_id}:{a['id']}", category="app", severity="high",
            title=f"Application tierce à haut risque : {a['name']}",
            detail=f"{'Éditeur vérifié' if a.get('verified') else 'Éditeur NON vérifié'} {a.get('publisher', '')} — "
                   f"{', '.join(a.get('high_risk_scopes') or [])}",
            link=f"/identity-hygiene/{connector_id}", data={"sp_id": a["id"]},
        ))
    for p in (snapshot.get("admins") or {}).get("people") or []:
        if "Sans MFA" in (p.get("flags") or []):
            out.append(AlertCandidate(
                key=f"admin-nomfa:{connector_id}:{p['upn'].lower()}", category="identity", severity="critical",
                title=f"Administrateur sans MFA : {p['upn']}", detail=", ".join(p.get("roles") or []),
                link=f"/identity-hygiene/{connector_id}",
            ))
    return out


def mfa_change_alerts(mailbox: dict, added: list[dict], sign_ins: list[dict], now: datetime) -> list[AlertCandidate]:
    """Nouvelle méthode MFA sur un compte suivi. Critique si elle suit (24 h avant) une connexion réussie depuis un pays
    inhabituel ou une rafale d'échecs : l'attaquant ajoute son propre téléphone / Authenticator dès qu'il entre."""
    out = []
    for m in added:
        at = m["at"]
        window = [s for s in sign_ins if at - timedelta(hours=24) <= s["at"] <= at + timedelta(minutes=30)]
        history = {s.get("country") for s in sign_ins if s["at"] < at - timedelta(hours=24) and s.get("success") and s.get("country")}
        odd = sorted({s["country"] for s in window if s.get("success") and s.get("country") and history and s["country"] not in history})
        failures = sum(1 for s in window if not s.get("success"))
        suspicious = bool(odd) or failures >= 5
        why = (f" après une connexion depuis {', '.join(odd)}" if odd else "") + (f" et {failures} échec(s) de connexion" if failures >= 5 else "")
        out.append(AlertCandidate(
            key=f"mfa-added:{mailbox['id']}:{m['method_id']}", category="identity",
            severity="critical" if suspicious else "medium",
            title=f"{'MFA ajouté de façon suspecte' if suspicious else 'Nouvelle méthode MFA'} : {mailbox['upn']}",
            detail=f"{m.get('label') or m.get('kind')}{' — ' + m['detail'] if m.get('detail') else ''}, ajoutée le "
                   f"{at.strftime('%Y-%m-%d %H:%M')} UTC{why}. Si l'utilisateur ne l'a pas ajoutée, lance la procédure d'urgence.",
            link=f"/mailboxes/{mailbox['id']}",
        ))
    return out


def reconcile(open_keys: set[str], candidates: Iterable[AlertCandidate]) -> tuple[list[AlertCandidate], set[str]]:
    """Renvoie (nouvelles alertes à créer, clés d'alertes ouvertes devenues sans objet → à résoudre)."""
    by_key = {c.key: c for c in candidates}
    new = [c for k, c in by_key.items() if k not in open_keys]
    gone = {k for k in open_keys if k not in by_key}
    return new, gone
