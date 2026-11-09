"""Partage OneDrive / SharePoint : parsing des permissions Graph et niveau de risque.

Fonctions pures (pas de réseau) : elles reçoivent le JSON brut d'une permission Graph.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Optional


class Risk(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


RISK_ORDER = [Risk.CRITICAL, Risk.HIGH, Risk.MEDIUM, Risk.LOW, Risk.INFO]


@dataclass(frozen=True)
class Entry:
    """Une permission de partage sur un élément (lien ou partage direct)."""
    permission_id: str
    kind: str                       # "link" | "direct"
    scope: str                      # anonymous | organization | users | direct
    link_type: str                  # view | edit | embed | "" (direct)
    roles: tuple[str, ...]
    link_url: str = ""
    expires_at: Optional[str] = None
    has_password: bool = False
    prevents_download: bool = False
    grantees: tuple[str, ...] = ()
    external: bool = False
    inherited: bool = False

    @property
    def can_edit(self) -> bool:
        return self.link_type == "edit" or "write" in self.roles or "owner" in self.roles


def classify(e: Entry) -> Risk:
    """Niveau de risque d'une permission de partage."""
    if e.scope == "anonymous":
        if e.can_edit:
            return Risk.CRITICAL
        return Risk.MEDIUM if e.expires_at else Risk.HIGH
    if e.external:
        return Risk.MEDIUM
    if e.scope == "organization":
        return Risk.MEDIUM if e.can_edit else Risk.LOW
    return Risk.INFO


UNCONFIRMED_NOTE = ("Microsoft signale ce lien « anonyme », mais le fichier est partagé avec des personnes "
                    "précises (shared.scope = users) : probablement un lien Teams/SharePoint, pas un lien public.")


def reconcile_anonymous(entry: Entry, item_scope: Optional[str]) -> tuple[Entry, str]:
    """Recoupe un lien « anonyme » avec le périmètre réel de l'élément (facette `shared.scope`).

    Graph étiquette parfois « anonymous » des permissions qui sont en réalité des partages
    à personnes précises (liens Teams, sans URL). Si l'élément n'est PAS réellement partagé
    anonymement, le lien est déclassé en « personnes précises » avec une note ; sinon (ou si
    la facette est inconnue) on garde le niveau d'alerte, par prudence. Renvoie (entrée, note)."""
    from dataclasses import replace
    if entry.scope == "anonymous" and item_scope in ("users", "organization"):
        return replace(entry, scope="users"), UNCONFIRMED_NOTE
    return entry, ""


def _identity_label(i: dict) -> tuple[str, bool]:
    """(libellé, est_externe) pour une identité Graph (user / siteUser / group…)."""
    for key in ("user", "siteUser", "group", "siteGroup", "application"):
        ident = i.get(key)
        if not ident:
            continue
        login = ident.get("loginName") or ""
        label = ident.get("email") or ident.get("displayName") or login or "?"
        return label, "#ext#" in login.lower()
    return "?", False


def parse_permission(p: dict, internal_domains: Iterable[str] = ()) -> Optional[Entry]:
    """Convertit une permission Graph en Entry ; None si ce n'est pas un partage à signaler
    (propriétaire seul, permission héritée du dossier parent, droits sans destinataire)."""
    if p.get("inheritedFrom"):
        return None  # déjà présente sur l'élément parent : évite les doublons
    roles = tuple(p.get("roles") or [])
    internal = {d.lower() for d in internal_domains}
    link = p.get("link")

    grantees: list[str] = []
    external = False
    identities = list(p.get("grantedToIdentitiesV2") or [])
    if p.get("grantedToV2"):
        identities.append(p["grantedToV2"])
    for ident in identities:
        label, ext = _identity_label(ident)
        grantees.append(label)
        domain = label.rsplit("@", 1)[-1].lower() if "@" in label else ""
        external = external or ext or bool(domain and internal and domain not in internal)
    inv = (p.get("invitation") or {}).get("email")
    if inv:
        grantees.append(inv)
        d = inv.rsplit("@", 1)[-1].lower()
        external = external or bool(internal and d not in internal)

    if link:
        return Entry(
            permission_id=p.get("id", ""), kind="link", scope=link.get("scope", "users"),
            link_type=link.get("type", ""), roles=roles, link_url=link.get("webUrl", ""),
            expires_at=p.get("expirationDateTime"), has_password=bool(p.get("hasPassword")),
            prevents_download=bool(link.get("preventsDownload")), grantees=tuple(grantees),
            external=external,
        )
    if roles == ("owner",) or not grantees:
        return None
    return Entry(
        permission_id=p.get("id", ""), kind="direct", scope="direct", link_type="", roles=roles,
        expires_at=p.get("expirationDateTime"), grantees=tuple(grantees), external=external,
    )


@dataclass
class SharedRecord:
    """Un partage à signaler, avec l'élément qui le porte (forme stockée dans le rapport d'audit)."""
    drive_id: str
    drive_label: str
    item_id: str
    name: str
    path: str
    web_url: str
    is_folder: bool
    entry: Entry
    risk: Risk = Risk.INFO
    note: str = ""

    def to_dict(self) -> dict:
        e = self.entry
        return {
            "drive_id": self.drive_id, "drive_label": self.drive_label, "item_id": self.item_id,
            "name": self.name, "path": self.path, "web_url": self.web_url, "is_folder": self.is_folder,
            "permission_id": e.permission_id, "kind": e.kind, "scope": e.scope, "link_type": e.link_type,
            "roles": list(e.roles), "link_url": e.link_url, "expires_at": e.expires_at,
            "has_password": e.has_password, "grantees": list(e.grantees), "external": e.external,
            "risk": self.risk.value, "note": self.note,
        }


def summarize(records: list[dict]) -> dict:
    """Compteurs pour le tableau de bord (à partir des enregistrements sérialisés)."""
    by_risk = {r.value: 0 for r in RISK_ORDER}
    for r in records:
        by_risk[r["risk"]] += 1
    return {
        "total": len(records),
        "anonymous": sum(1 for r in records if r["scope"] == "anonymous"),
        "organization": sum(1 for r in records if r["scope"] == "organization"),
        "external": sum(1 for r in records if r["external"]),
        "anonymous_edit": sum(1 for r in records if r["scope"] == "anonymous" and r["link_type"] == "edit"),
        "no_expiry_anonymous": sum(1 for r in records if r["scope"] == "anonymous" and not r["expires_at"]),
        "by_risk": by_risk,
    }
