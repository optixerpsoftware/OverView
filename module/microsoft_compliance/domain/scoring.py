from __future__ import annotations

from typing import Iterable, Optional

from module.microsoft_compliance.domain.entities import Finding, Severity

WEIGHTS = {
    Severity.CRITICAL: 10,
    Severity.HIGH: 6,
    Severity.MEDIUM: 3,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


def compute_score(findings: Iterable[Finding]) -> Optional[float]:
    """Score global 0..100 : moyenne pondérée par la gravité des contrôles évaluables.

    Les contrôles indisponibles (licence/permission) ou en erreur sont exclus : ils ne
    pénalisent pas le score. Renvoie None si aucun contrôle n'a pu être évalué."""
    total = got = 0.0
    for f in findings:
        w = WEIGHTS[f.severity]
        if not f.scored or w == 0:
            continue
        total += w
        got += w * max(0.0, min(1.0, f.score))
    if total == 0:
        return None
    return round(100 * got / total, 1)
