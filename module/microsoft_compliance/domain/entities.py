from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"   # licence ou permission manquante : exclu du score
    ERROR = "error"               # échec inattendu : exclu du score


class Category(str, Enum):
    MFA = "mfa"
    ACCESS = "access"             # rôles admin, invités, comptes inactifs
    POLICIES = "policies"         # accès conditionnel, Security Defaults
    POSTURE = "posture"           # Secure Score, applications


@dataclass(frozen=True)
class Finding:
    """Résultat d'un contrôle de gouvernance."""
    code: str
    category: Category
    title: str
    severity: Severity
    status: Status
    summary: str = ""
    details: tuple[str, ...] = ()
    recommendation: str = ""
    score: float = 1.0                 # 0..1 : degré de conformité (1 = conforme)
    requires: str = ""                 # permission / licence nécessaire (affichée si indisponible)

    @property
    def scored(self) -> bool:
        return self.status in (Status.PASS, Status.FAIL)


@dataclass
class ScanResult:
    score: Optional[float]             # 0..100, None si rien n'est évaluable
    findings: list[Finding] = field(default_factory=list)
    licenses: dict = field(default_factory=dict)

    def by_status(self, status: Status) -> list[Finding]:
        return [f for f in self.findings if f.status == status]
