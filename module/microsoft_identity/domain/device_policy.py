"""Politique d'inscription des appareils Entra ID (deviceRegistrationPolicy) : lecture, validation
et application de changements. Fonctions pures sur le JSON Graph : aucun appel réseau.

Le JSON brut est conservé tel quel : on ne modifie que les champs autorisés et on renvoie le reste
intact (la mise à jour Graph est un PUT de l'objet complet)."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Optional

_ODATA = "@odata.type"
_MEMBERSHIP = {
    "all": "#microsoft.graph.allDeviceRegistrationMembership",
    "none": "#microsoft.graph.noDeviceRegistrationMembership",
    "selected": "#microsoft.graph.enumeratedDeviceRegistrationMembership",
}
QUOTA_MIN, QUOTA_MAX = 1, 100
MAX_IDS = 50
_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@dataclass(frozen=True)
class DeviceRegistrationSettings:
    user_device_quota: Optional[int]
    mfa_required: bool
    join_scope: str                 # all | none | selected
    join_users: int
    join_groups: int
    join_configurable: bool
    register_scope: str
    register_users: int
    register_groups: int
    register_configurable: bool
    laps_enabled: bool
    join_user_ids: tuple[str, ...] = field(default=())
    join_group_ids: tuple[str, ...] = field(default=())


def _members(membership: Optional[dict]) -> tuple[str, list[str], list[str]]:
    m = membership or {}
    t = m.get(_ODATA, "")
    for name, odata in _MEMBERSHIP.items():
        if t == odata:
            return name, list(m.get("users") or []), list(m.get("groups") or [])
    return "all", [], []


def _mfa_on(value) -> bool:
    return str(value).lower() in ("required", "1", "true")


def parse(raw: dict) -> DeviceRegistrationSettings:
    join = raw.get("azureADJoin") or {}
    reg = raw.get("azureADRegistration") or {}
    js, ju, jg = _members(join.get("allowedToJoin"))
    rs, ru, rg = _members(reg.get("allowedToRegister"))
    return DeviceRegistrationSettings(
        user_device_quota=raw.get("userDeviceQuota"),
        mfa_required=_mfa_on(raw.get("multiFactorAuthConfiguration", "notRequired")),
        join_scope=js, join_users=len(ju), join_groups=len(jg),
        join_configurable=bool(join.get("isAdminConfigurable", True)),
        register_scope=rs, register_users=len(ru), register_groups=len(rg),
        register_configurable=bool(reg.get("isAdminConfigurable", True)),
        laps_enabled=bool((raw.get("localAdminPassword") or {}).get("isEnabled")),
        join_user_ids=tuple(ju), join_group_ids=tuple(jg),
    )


def _ids(value, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_IDS:
        raise ValueError(f"{label} : liste d'identifiants attendue (maximum {MAX_IDS}).")
    out = []
    for v in value:
        if not isinstance(v, str) or not _GUID.match(v.strip()):
            raise ValueError(f"{label} : identifiant invalide « {v} » (format GUID attendu).")
        v = v.strip().lower()
        if v not in out:
            out.append(v)
    return out


def apply_changes(raw: dict, changes: dict) -> dict:
    """Renvoie une copie de `raw` avec les `changes` appliqués (clés reconnues uniquement).

    Clés : user_device_quota (int), mfa_required (bool), laps_enabled (bool), register_scope ('all'|'none'),
    join_scope ('all'|'none'|'selected') avec join_groups / join_users (listes de GUID) pour 'selected'.
    Lève ValueError pour toute valeur invalide ou réglage non modifiable."""
    known = {"user_device_quota", "mfa_required", "join_scope", "register_scope", "laps_enabled",
             "join_groups", "join_users"}
    unknown = set(changes) - known
    if unknown:
        raise ValueError(f"Réglage inconnu : {', '.join(sorted(unknown))}.")
    out = copy.deepcopy(raw)
    cur = parse(raw)

    if "user_device_quota" in changes:
        q = changes["user_device_quota"]
        if isinstance(q, bool) or not isinstance(q, int) or not QUOTA_MIN <= q <= QUOTA_MAX:
            raise ValueError(f"Le quota d'appareils par utilisateur doit être un entier de {QUOTA_MIN} à {QUOTA_MAX}.")
        out["userDeviceQuota"] = q

    if "mfa_required" in changes:
        if not isinstance(changes["mfa_required"], bool):
            raise ValueError("mfa_required doit être vrai ou faux.")
        legacy = str(raw.get("multiFactorAuthConfiguration", "")) in ("0", "1")
        on = changes["mfa_required"]
        out["multiFactorAuthConfiguration"] = ("1" if on else "0") if legacy else ("required" if on else "notRequired")

    # --- jonction (Entra join) : tous / personne / sélection de groupes et d'utilisateurs ---
    if {"join_scope", "join_groups", "join_users"} & set(changes):
        target = changes.get("join_scope", cur.join_scope)
        if target not in _MEMBERSHIP:
            raise ValueError("join_scope : « all », « none » ou « selected ».")
        if target != "selected" and ({"join_groups", "join_users"} & set(changes)):
            raise ValueError("Des groupes ou utilisateurs ne s'appliquent qu'avec join_scope = « selected ».")
        groups = _ids(changes["join_groups"], "Groupes") if "join_groups" in changes else list(cur.join_group_ids)
        users = _ids(changes["join_users"], "Utilisateurs") if "join_users" in changes else list(cur.join_user_ids)
        if target == "selected" and cur.join_scope != "selected" and "join_users" not in changes:
            users = []   # on bascule vers une sélection : on ne reprend pas d'anciens utilisateurs
        if target == "selected" and not groups and not users:
            raise ValueError("Une sélection doit contenir au moins un groupe ou un utilisateur.")
        changed = (target != cur.join_scope) or (
            target == "selected" and (set(groups) != set(cur.join_group_ids) or set(users) != set(cur.join_user_ids)))
        if changed:
            if not cur.join_configurable:
                raise ValueError("Ce réglage n'est pas modifiable par un administrateur sur ce tenant.")
            membership = {_ODATA: _MEMBERSHIP[target]}
            if target == "selected":
                membership.update({"users": users, "groups": groups})
            out.setdefault("azureADJoin", {})["allowedToJoin"] = membership

    # --- enregistrement (BYOD) : seulement tous / personne, et seulement si modifiable ---
    if "register_scope" in changes:
        target = changes["register_scope"]
        if target not in ("all", "none"):
            raise ValueError("register_scope : « all » ou « none ».")
        if target != cur.register_scope:
            if not cur.register_configurable:
                raise ValueError("Ce réglage n'est pas modifiable par un administrateur sur ce tenant.")
            out.setdefault("azureADRegistration", {})["allowedToRegister"] = {_ODATA: _MEMBERSHIP[target]}

    if "laps_enabled" in changes:
        if not isinstance(changes["laps_enabled"], bool):
            raise ValueError("laps_enabled doit être vrai ou faux.")
        out.setdefault("localAdminPassword", {})["isEnabled"] = changes["laps_enabled"]
    return out


_LABELS = {
    "user_device_quota": "Quota d'appareils par utilisateur",
    "mfa_required": "MFA exigé à l'inscription",
    "join_scope": "Qui peut joindre des appareils",
    "register_scope": "Qui peut enregistrer des appareils",
    "laps_enabled": "Mot de passe administrateur local (LAPS)",
}
_SCOPE_FR = {"all": "tous les utilisateurs", "none": "personne", "selected": "une sélection"}


def _show(key: str, s: DeviceRegistrationSettings) -> str:
    v = getattr(s, key)
    if key in ("mfa_required", "laps_enabled"):
        return "oui" if v else "non"
    if key == "join_scope":
        label = _SCOPE_FR.get(v, str(v))
        if v == "selected":
            label += f" ({s.join_groups} groupe{'s' if s.join_groups > 1 else ''}, {s.join_users} utilisateur{'s' if s.join_users > 1 else ''})"
        return label
    if key == "register_scope":
        return _SCOPE_FR.get(v, str(v))
    return str(v)


def diff(before: DeviceRegistrationSettings, after: DeviceRegistrationSettings) -> list[str]:
    """Liste lisible des changements (pour la confirmation et le journal d'audit)."""
    out = []
    for key, label in _LABELS.items():
        if getattr(before, key) != getattr(after, key):
            out.append(f"{label} : {_show(key, before)} → {_show(key, after)}")
    if (before.join_scope == after.join_scope == "selected"
            and (set(before.join_group_ids) != set(after.join_group_ids)
                 or set(before.join_user_ids) != set(after.join_user_ids))):
        out.append(f"Sélection autorisée à joindre : {_show('join_scope', before)} → {_show('join_scope', after)}")
    return out
