"""Protection automatique : limites de fréquence (rate limits) par organisation.

Fonctions pures : à partir des appareils récemment inscrits et des connexions, déterminent qui dépasse quelle
limite, et si l'on doit seulement alerter ou désactiver le compte. Aucun accès réseau ni base.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional

MODES = ("off", "monitor", "enforce")
EMAIL_WINDOWS = (15, 30, 60, 120)   # désactivé · alerter seulement · alerter ET désactiver le compte


@dataclass(frozen=True)
class Limits:
    mode: str = "monitor"
    devices_per_user_per_day: int = 3          # appareils joints / inscrits par un même compte en 24 h
    devices_per_org_per_day: int = 20          # pour tout le tenant en 24 h (alerte : pas de compte unique à cibler)
    failed_signins_per_user_per_hour: int = 20 # échecs de connexion d'un compte suivi en 1 h
    disable_on_device_limit: bool = True
    disable_on_signin_limit: bool = False      # par défaut non : un attaquant pourrait verrouiller un compte légitime
    emails_per_user_window: int = 200          # destinataires (envois) d'un même compte dans la fenêtre
    email_window_minutes: int = 60             # fenêtre glissante : 15, 30, 60 ou 120 minutes
    disable_on_email_limit: bool = True        # un compte qui spamme est presque toujours compromis
    neutralize_rules: bool = True              # désactiver une règle Outlook qui transfère vers l'extérieur
    neutralize_forwarding: bool = True         # retirer un transfert SMTP de boîte vers l'extérieur
    block_risky_consents: bool = True          # bloquer une appli tierce nouvellement autorisée avec des droits sensibles
    exclusions: tuple[str, ...] = ()           # comptes jamais désactivés automatiquement (ex. compte d'urgence)

    def validate(self) -> "Limits":
        if self.mode not in MODES:
            raise ValueError("Mode invalide.")
        for name, v, lo, hi in (("devices_per_user_per_day", self.devices_per_user_per_day, 1, 100),
                                ("devices_per_org_per_day", self.devices_per_org_per_day, 1, 10000),
                                ("failed_signins_per_user_per_hour", self.failed_signins_per_user_per_hour, 3, 10000)):
            if not isinstance(v, int) or not lo <= v <= hi:
                raise ValueError(f"{name} doit être entre {lo} et {hi}.")
        if self.email_window_minutes not in EMAIL_WINDOWS:
            raise ValueError("email_window_minutes : 15, 30, 60 ou 120.")
        if not isinstance(self.emails_per_user_window, int) or not 10 <= self.emails_per_user_window <= 100000:
            raise ValueError("emails_per_user_window doit être entre 10 et 100000.")
        if len(self.exclusions) > 50 or any("@" not in e for e in self.exclusions):
            raise ValueError("Exclusions : 50 adresses au maximum, au format utilisateur@domaine.")
        return self


@dataclass(frozen=True)
class Breach:
    rule: str                   # devices_user | devices_org | signin_failures
    key: str                    # clé de déduplication (un déclenchement par compte, règle et jour)
    upn: str                    # compte en cause ("" pour la règle globale)
    user_id: str
    count: int
    limit: int
    detail: str
    disable: bool               # faut-il désactiver le compte ?
    blocked_reason: str = ""    # pourquoi on NE désactive PAS alors que la règle le voudrait
    items: tuple[str, ...] = field(default_factory=tuple)
    target: str = ""            # objet à neutraliser : règle (mailbox_upn|rule_id), boîte, principal de service


def _dt(v) -> Optional[datetime]:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def evaluate(limits: Limits, devices: Iterable[dict], sign_ins: Iterable[dict], now: datetime,
             protected_upns: Iterable[str] = (), sent: Optional[Iterable[dict]] = None,
             internal_domains: Iterable[str] = ()) -> list[Breach]:
    """`devices` : [{id, displayName, registrationDateTime, owners: [{id, userPrincipalName}]}]
    `sign_ins` : [{at, success, upn, user_id}] (comptes suivis). `protected_upns` : administrateurs (jamais désactivés)."""
    if limits.mode == "off":
        return []
    day = now.date().isoformat()
    shield = {u.lower() for u in limits.exclusions} | {u.lower() for u in protected_upns}
    out: list[Breach] = []

    def guard(upn: str, wants: bool) -> tuple[bool, str]:
        if not wants:
            return False, ""
        if limits.mode != "enforce":
            return False, "Mode surveillance : alerte seulement."
        if upn.lower() in shield:
            return False, "Compte protégé (administrateur ou exclusion) : non désactivé."
        return True, ""

    # 1. Appareils inscrits sur 24 h glissantes, par compte et pour le tenant.
    recent = [d for d in devices if (_dt(d.get("registrationDateTime")) or now - timedelta(days=2)) >= now - timedelta(hours=24)]
    per_user: dict[str, list[dict]] = defaultdict(list)
    ids: dict[str, str] = {}
    for d in recent:
        for o in d.get("owners") or []:
            upn = (o.get("userPrincipalName") or "").strip()
            if upn:
                per_user[upn].append(d)
                ids[upn] = o.get("id", "")
    for upn, devs in sorted(per_user.items()):
        if len(devs) > limits.devices_per_user_per_day:
            disable, why = guard(upn, limits.disable_on_device_limit)
            names = tuple(sorted(d.get("displayName") or d.get("id") for d in devs))
            out.append(Breach("devices_user", f"devices_user:{upn.lower()}:{day}", upn, ids.get(upn, ""), len(devs),
                              limits.devices_per_user_per_day,
                              f"{len(devs)} appareils inscrits en 24 h (limite {limits.devices_per_user_per_day}) : {', '.join(names)}",
                              disable, why, names))
    if len(recent) > limits.devices_per_org_per_day:
        top = sorted(per_user.items(), key=lambda kv: -len(kv[1]))[:5]
        out.append(Breach("devices_org", f"devices_org:{day}", "", "", len(recent), limits.devices_per_org_per_day,
                          f"{len(recent)} appareils inscrits dans le tenant en 24 h (limite {limits.devices_per_org_per_day}). "
                          f"Principaux comptes : {', '.join(f'{u} ({len(d)})' for u, d in top) or '—'}", False))

    # 2. Échecs de connexion sur 1 h glissante, par compte suivi.
    fails: dict[str, list[dict]] = defaultdict(list)
    for s in sign_ins:
        at = _dt(s.get("at"))
        if at and at >= now - timedelta(hours=1) and not s.get("success"):
            fails[s.get("upn") or ""].append(s)
    for upn, rows in sorted(fails.items()):
        if upn and len(rows) > limits.failed_signins_per_user_per_hour:
            disable, why = guard(upn, limits.disable_on_signin_limit)
            out.append(Breach("signin_failures", f"signin_failures:{upn.lower()}:{now.strftime('%Y-%m-%dT%H')}", upn,
                              rows[0].get("user_id", ""), len(rows), limits.failed_signins_per_user_per_hour,
                              f"{len(rows)} échecs de connexion en 1 h (limite {limits.failed_signins_per_user_per_hour}) "
                              f"depuis {', '.join(sorted({r.get('ip') or '?' for r in rows})[:5])}", disable, why))
    # 3. Courriels envoyés (nombre de destinataires) par compte interne dans la fenêtre glissante.
    if sent is not None:
        internal = {d.lower() for d in internal_domains}
        start = now - timedelta(minutes=limits.email_window_minutes)
        per_sender: dict[str, list[dict]] = defaultdict(list)
        for m in sent:
            sender = (m.get("sender") or "").lower()
            at = _dt(m.get("at"))
            if sender and at and at >= start and (not internal or sender.rsplit("@", 1)[-1] in internal):
                per_sender[sender].append(m)
        bucket = start.replace(minute=(start.minute // limits.email_window_minutes) * limits.email_window_minutes
                               if limits.email_window_minutes < 60 else 0, second=0, microsecond=0)
        for sender, rows in sorted(per_sender.items()):
            if len(rows) > limits.emails_per_user_window:
                disable, why = guard(sender, limits.disable_on_email_limit)
                messages = {r.get("message_id") or id(r) for r in rows}
                external = sum(1 for r in rows if internal and (r.get("recipient") or "").rsplit("@", 1)[-1].lower() not in internal)
                subjects = sorted({(r.get("subject") or "(sans objet)")[:60] for r in rows})[:3]
                out.append(Breach(
                    "emails_user", f"emails_user:{sender}:{bucket.strftime('%Y-%m-%dT%H:%M')}", sender, "", len(rows),
                    limits.emails_per_user_window,
                    f"{len(rows)} envois en {limits.email_window_minutes} min (limite {limits.emails_per_user_window}) : "
                    f"{len(messages)} message(s), {external} destinataire(s) externe(s). Objets : {' · '.join(subjects)}",
                    disable, why, tuple(subjects)))
    return out


# ---------------------------------------------------------------- neutralisation (règles, transferts, applis)
def _act(limits: Limits, flag: bool) -> tuple[bool, str]:
    """Neutraliser une règle / un transfert / une appli ne verrouille personne : les admins ne sont pas exemptés."""
    if not flag:
        return False, "Réponse automatique désactivée pour ce cas : alerte seulement."
    if limits.mode != "enforce":
        return False, "Mode surveillance : alerte seulement."
    return True, ""


def _external(addresses: Iterable[str], internal: set[str]) -> list[str]:
    return sorted({a.lower() for a in addresses if a and "@" in a and a.rsplit("@", 1)[-1].lower() not in internal})


def rule_breaches(limits: Limits, rules: Iterable[dict], internal_domains: Iterable[str]) -> list[Breach]:
    """`rules` : [{id, rule_id, mailbox_upn, display_name, is_enabled, actions}] (règles Outlook synchronisées)."""
    if limits.mode == "off":
        return []
    internal = {d.lower() for d in internal_domains}
    out = []
    for r in rules:
        if not r.get("is_enabled", True):
            continue
        acts = r.get("actions") or {}
        targets = [((a or {}).get("emailAddress") or {}).get("address", "")
                   for k in ("forwardTo", "forwardAsAttachmentTo", "redirectTo") for a in acts.get(k) or []]
        ext = _external(targets, internal)
        if not ext:
            continue
        act, why = _act(limits, limits.neutralize_rules)
        out.append(Breach("malicious_rule", f"malicious_rule:{r['id']}", r.get("mailbox_upn", ""), "", len(ext), 0,
                          f"Règle « {r.get('display_name', '')} » transfère vers l'extérieur : {', '.join(ext)}",
                          act, why, tuple(ext), f"{r.get('mailbox_upn', '')}|{r.get('rule_id', '')}"))
    return out


def forwarding_breaches(limits: Limits, mailboxes: Iterable[dict], internal_domains: Iterable[str]) -> list[Breach]:
    """`mailboxes` : sorties Get-Mailbox {UserPrincipalName, ForwardingSmtpAddress, DeliverToMailboxAndForward}."""
    if limits.mode == "off":
        return []
    internal = {d.lower() for d in internal_domains}
    out = []
    for m in mailboxes:
        fwd = str(m.get("ForwardingSmtpAddress") or "")
        addr = fwd.split(":", 1)[-1] if fwd else ""
        ext = _external([addr], internal)
        if not ext:
            continue
        upn = m.get("UserPrincipalName") or m.get("PrimarySmtpAddress") or "?"
        keep = m.get("DeliverToMailboxAndForward")
        act, why = _act(limits, limits.neutralize_forwarding)
        out.append(Breach("mailbox_forwarding", f"mailbox_forwarding:{upn.lower()}:{ext[0]}", upn, "", 1, 0,
                          f"Transfert SMTP de toute la boîte vers {ext[0]}"
                          f"{' (copie conservée)' if keep else ' (SANS copie dans la boîte)'} — invisible dans Outlook.",
                          act, why, tuple(ext), upn))
    return out


def consent_breaches(limits: Limits, apps: Iterable[dict], known_ids: Optional[set[str]]) -> list[Breach]:
    """`apps` : applis tierces de `hygiene.app_consents`. `known_ids` : principaux de service déjà vus (None = première
    évaluation : on mémorise sans alerter, pour ne pas signaler d'un coup tout l'existant)."""
    if limits.mode == "off" or known_ids is None:
        return []
    out = []
    for a in apps:
        if a["id"] in known_ids or a.get("risk") not in ("high", "medium") or not a.get("enabled", True):
            continue
        act, why = _act(limits, limits.block_risky_consents and a["risk"] == "high")
        who = sorted({d["who"] for d in a.get("delegated") or []}) or ["consentement administrateur (permissions d'application)"]
        out.append(Breach("app_consent", f"app_consent:{a['id']}", "", "", len(a.get("high_risk_scopes") or []), 0,
                          f"Nouvelle appli tierce « {a['name']} » ({'éditeur vérifié' if a.get('verified') else 'éditeur NON vérifié'}) "
                          f"autorisée par {', '.join(who)} : {', '.join(a.get('high_risk_scopes') or []) or 'permissions étendues'}",
                          act, why, tuple(a.get("high_risk_scopes") or ()), a["id"]))
    return out
