"""Alternatives sans licence Entra ID P1 : reconstitue les données des rapports premium
à partir de sources disponibles avec une licence Microsoft 365 standard."""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Iterable, Optional

# type Graph de la méthode -> nom utilisé par le rapport userRegistrationDetails
_METHOD_NAMES = {
    "microsoftAuthenticatorAuthenticationMethod": "microsoftAuthenticatorPush",
    "phoneAuthenticationMethod": "mobilePhone",
    "fido2AuthenticationMethod": "fido2",
    "windowsHelloForBusinessAuthenticationMethod": "windowsHelloForBusiness",
    "softwareOathAuthenticationMethod": "softwareOneTimePasscode",
    "temporaryAccessPassAuthenticationMethod": "temporaryAccessPass",
    "emailAuthenticationMethod": "email",
    "platformCredentialAuthenticationMethod": "windowsHelloForBusiness",
}
# méthodes qui constituent un second facteur (le courriel de récupération et le mot de passe n'en sont pas)
_MFA = {"microsoftAuthenticatorPush", "mobilePhone", "fido2", "windowsHelloForBusiness",
        "softwareOneTimePasscode", "temporaryAccessPass"}


def registration_from_methods(upn: str, methods: Iterable[dict], is_admin: bool) -> dict:
    """Équivalent d'une ligne de userRegistrationDetails, calculé depuis /authentication/methods."""
    registered = sorted({
        _METHOD_NAMES[t] for m in methods
        if (t := (m.get("@odata.type") or "").rsplit(".", 1)[-1]) in _METHOD_NAMES
    })
    return {
        "userPrincipalName": upn,
        "isAdmin": is_admin,
        "isMfaRegistered": bool(set(registered) & _MFA),
        "methodsRegistered": registered,
    }


def is_concealed(upn: str) -> bool:
    """Les rapports Microsoft 365 masquent par défaut les noms (hachés) : pas d'« @ »."""
    return "@" not in (upn or "")


def parse_activity_report(csv_text: str) -> Optional[dict[str, Optional[str]]]:
    """Rapport getOffice365ActiveUserDetail (CSV) -> {upn: date ISO de la dernière activité | None}.

    La dernière activité est le maximum des colonnes « … Last Activity Date » (Exchange, OneDrive,
    SharePoint, Teams…). Renvoie None si les noms sont masqués (jointure impossible avec les comptes)."""
    text = csv_text.lstrip("﻿")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return {}
    upn_col = next((c for c in rows[0] if c.strip().lower() == "user principal name"), None)
    if upn_col is None:
        return None
    date_cols = [c for c in rows[0] if re.search(r"last activity date$", c.strip(), re.I)]
    out: dict[str, Optional[str]] = {}
    for r in rows:
        upn = (r.get(upn_col) or "").strip().lower()
        if is_concealed(upn):
            return None
        dates = [d for c in date_cols if (d := (r.get(c) or "").strip())]
        out[upn] = max(dates) if dates else None  # AAAA-MM-JJ : le tri lexical suffit
    return out


def activity_to_iso(date: Optional[str]) -> Optional[str]:
    if not date:
        return None
    try:
        return datetime.fromisoformat(date).strftime("%Y-%m-%dT00:00:00+00:00")
    except ValueError:
        return None
