from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

from asgiref.sync import async_to_sync

from shared_kernel.aio import db
from shared_kernel.realtime import apublish

from module.microsoft_compliance.domain import checks
from module.microsoft_compliance.domain.entities import (
    Category, Finding, ScanResult, Severity, Status,
)
from module.microsoft_compliance.domain.ports import (
    ComplianceGateway, DataUnavailable, SnapshotRepository,
)
from module.microsoft_compliance.domain.scoring import compute_score
from module.microsoft_compliance.infrastructure.graph_gateway import GraphComplianceGateway
from module.microsoft_compliance.infrastructure.repositories import DjangoSnapshotRepository
from module.microsoft_connector.application.graph_factory import build_graph
from module.microsoft_connector.domain.repositories import ConnectorRepository
from module.microsoft_connector.infrastructure.repositories import DjangoConnectorRepository


class ConnectorUnavailable(Exception):
    pass


@dataclass(frozen=True)
class CheckSpec:
    """Un contrôle : métadonnées (pour produire un constat si les données manquent) + fonction pure."""
    code: str
    category: Category
    title: str
    severity: Severity
    needs: tuple[str, ...]            # données obligatoires
    fn: Callable[[dict], Finding]
    optional: tuple[str, ...] = ()    # données facultatives (None si indisponibles)
    any_of: bool = False              # au moins une des `optional` doit être lisible


def _opt(d: dict, key: str) -> Any:
    return d.get(key)


SPECS: list[CheckSpec] = [
    CheckSpec("mfa.coverage", Category.MFA, "Couverture MFA", Severity.HIGH, ("users", "registrations"),
              lambda d: checks.mfa_coverage(d["users"], d["registrations"])),
    CheckSpec("mfa.admins", Category.MFA, "MFA des comptes administrateurs", Severity.CRITICAL, ("registrations",),
              lambda d: checks.mfa_admins(d["registrations"])),
    CheckSpec("mfa.weak", Category.MFA, "Méthodes MFA faibles (SMS / appel)", Severity.MEDIUM, ("registrations",),
              lambda d: checks.mfa_weak_methods(d["registrations"])),
    CheckSpec("access.global_admins", Category.ACCESS, "Nombre d'administrateurs généraux", Severity.MEDIUM, ("roles",),
              lambda d: checks.global_admin_count(d["roles"])),
    CheckSpec("access.admin_guests", Category.ACCESS, "Invités avec un rôle privilégié", Severity.HIGH, ("roles",),
              lambda d: checks.admin_guests(d["roles"])),
    CheckSpec("access.guests", Category.ACCESS, "Comptes invités", Severity.INFO, ("users",),
              lambda d: checks.guests_overview(d["users"])),
    CheckSpec("access.stale", Category.ACCESS, "Comptes inactifs", Severity.MEDIUM, ("users",),
              lambda d: checks.stale_accounts(d["users"])),
    CheckSpec("policies.baseline", Category.POLICIES, "Protection de base de l'identité", Severity.CRITICAL, (),
              lambda d: checks.baseline_protection(_opt(d, "security_defaults"), _opt(d, "ca_policies")),
              optional=("security_defaults", "ca_policies"), any_of=True),
    CheckSpec("policies.mfa_all", Category.POLICIES, "MFA exigé pour tous les utilisateurs", Severity.HIGH, (),
              lambda d: checks.ca_mfa_for_all(_opt(d, "security_defaults"), _opt(d, "ca_policies")),
              optional=("security_defaults", "ca_policies"), any_of=True),
    CheckSpec("policies.legacy_auth", Category.POLICIES, "Authentification héritée bloquée", Severity.HIGH, (),
              lambda d: checks.ca_block_legacy(_opt(d, "security_defaults"), _opt(d, "ca_policies")),
              optional=("security_defaults", "ca_policies"), any_of=True),
    CheckSpec("posture.secure_score", Category.POSTURE, "Secure Score Microsoft", Severity.MEDIUM, ("secure_score",),
              lambda d: checks.secure_score(d["secure_score"], _opt(d, "score_profiles")),
              optional=("score_profiles",)),
    CheckSpec("posture.app_secrets", Category.POSTURE, "Secrets et certificats d'applications", Severity.MEDIUM,
              ("applications",), lambda d: checks.app_secrets(d["applications"])),
]

FETCHES = ("users", "registrations", "roles", "ca_policies", "security_defaults",
           "secure_score", "score_profiles", "applications")


def finding_to_dict(f: Finding) -> dict:
    return {
        "code": f.code, "category": f.category.value, "title": f.title, "severity": f.severity.value,
        "status": f.status.value, "summary": f.summary, "details": list(f.details),
        "recommendation": f.recommendation, "score": round(f.score, 3), "requires": f.requires,
    }


def summarize_licenses(skus: list[dict]) -> dict:
    plans = {p.get("servicePlanName") for s in skus for p in s.get("servicePlans", [])
             if p.get("provisioningStatus") == "Success"}
    return {
        "skus": sorted({s.get("skuPartNumber", "?") for s in skus}),
        "entra_p1": bool(plans & {"AAD_PREMIUM", "AAD_PREMIUM_P2"}),
        "entra_p2": "AAD_PREMIUM_P2" in plans,
    }


def _failed(spec: CheckSpec, status: Status, reason: str, requires: str = "") -> Finding:
    return Finding(spec.code, spec.category, spec.title, spec.severity, status, reason, requires=requires)


def evaluate(data: dict[str, Any]) -> list[Finding]:
    """Exécute tous les contrôles sur les données récupérées. `data[nom]` est une valeur ou
    une exception (DataUnavailable, autre) : un contrôle dépendant d'une donnée absente
    devient « indisponible » ou « erreur » et n'entre pas dans le score."""
    findings: list[Finding] = []
    for spec in SPECS:
        bad = next((data[n] for n in spec.needs if isinstance(data.get(n), BaseException)), None)
        if bad is not None:
            if isinstance(bad, DataUnavailable):
                findings.append(_failed(spec, Status.UNAVAILABLE, bad.reason, bad.requires))
            else:
                findings.append(_failed(spec, Status.ERROR, f"{type(bad).__name__}: {bad}"[:300]))
            continue
        usable = {k: (None if isinstance(data.get(k), BaseException) else data.get(k))
                  for k in spec.needs + spec.optional}
        if spec.any_of and all(usable[k] is None for k in spec.optional):
            err = next((data[k] for k in spec.optional if isinstance(data.get(k), BaseException)), None)
            req = getattr(err, "requires", "") if err else ""
            findings.append(_failed(spec, Status.UNAVAILABLE, "Données de politiques illisibles.", req))
            continue
        try:
            findings.append(spec.fn(usable))
        except Exception as e:  # un contrôle défectueux ne doit pas faire échouer le scan entier
            findings.append(_failed(spec, Status.ERROR, f"{type(e).__name__}: {e}"[:300]))
    return findings


class ComplianceService:
    """Analyse de gouvernance d'un tenant M365 (via son connecteur) + historique."""

    def __init__(
        self,
        connectors: Optional[ConnectorRepository] = None,
        snapshots: Optional[SnapshotRepository] = None,
        gateway_factory: Callable[[Any], ComplianceGateway] = lambda c: GraphComplianceGateway(build_graph(c)),
    ):
        self.connectors = connectors or DjangoConnectorRepository()
        self.snapshots = snapshots or DjangoSnapshotRepository()
        self.gateway_factory = gateway_factory

    def _connector(self, connector_id: uuid.UUID):
        c = self.connectors.get(connector_id)
        if c is None or not c.is_active:
            raise ConnectorUnavailable(str(connector_id))
        return c

    def scan(self, connector_id: uuid.UUID) -> dict:
        return async_to_sync(self.scan_async)(connector_id)

    async def scan_async(self, connector_id: uuid.UUID) -> dict:
        """Analyse du tenant : les 9 lectures Graph sont lancées en parallèle (`asyncio.gather`)."""
        connector = await db(self._connector)(connector_id)
        gw = self.gateway_factory(connector)
        names = FETCHES + ("licenses",)
        results = await asyncio.gather(*(getattr(gw, n)() for n in names), return_exceptions=True)
        data = dict(zip(names, results))
        # Échec total non lié aux permissions (secret invalide, réseau…) : ce n'est pas un résultat
        # d'analyse, on remonte l'erreur au lieu d'enregistrer un instantané vide.
        if all(isinstance(v, BaseException) for v in data.values()) \
                and not any(isinstance(v, DataUnavailable) for v in data.values()):
            raise next(iter(data.values()))
        lic = data.pop("licenses")
        licenses = summarize_licenses(lic) if not isinstance(lic, BaseException) else {"error": str(lic)[:200]}
        findings = evaluate(data)
        result = ScanResult(score=compute_score(findings), findings=findings, licenses=licenses)
        snapshot = await db(self.snapshots.add)(
            connector_id=connector.id, org_id=connector.org_id, score=result.score,
            findings=[finding_to_dict(f) for f in result.findings], licenses=result.licenses)
        await apublish("compliance.scan", {"connector_id": str(connector.id), "score": result.score})
        return snapshot

    def scan_all(self) -> dict:
        """Analyse tous les connecteurs actifs (appelé chaque jour par le worker)."""
        ok = failed = 0
        for c in self.connectors.list_active():
            try:
                self.scan(c.id)
                ok += 1
            except Exception:
                failed += 1
        return {"scanned": ok, "failed": failed}

    def latest(self, connector_id: uuid.UUID) -> Optional[dict]:
        self._connector(connector_id)
        return self.snapshots.latest(connector_id)

    def history(self, connector_id: uuid.UUID, limit: int = 60) -> list[dict]:
        self._connector(connector_id)
        return self.snapshots.history(connector_id, limit)
