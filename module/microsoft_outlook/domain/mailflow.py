"""Flux de messagerie (suivi des messages) et quarantaine Exchange Online : parsing, résumé, règles de libération.

Fonctions pures sur les lignes JSON renvoyées par les cmdlets Exchange (clés en PascalCase) : aucun appel réseau.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

# Types de quarantaine dont la libération est dangereuse : le message est reconnu comme logiciel malveillant ou
# hameçonnage à haute confiance. La libération exige une confirmation explicite supplémentaire.
RISKY_TYPES = {"Malware", "HighConfPhish"}
RELEASE_WORD = "LIBERER"
TRACE_STATUSES = {"Delivered", "Failed", "Pending", "Quarantined", "FilteredAsSpam", "Expanded", "GettingStatus"}
QUARANTINE_TYPES = {"Spam", "Phish", "HighConfPhish", "Malware", "Bulk", "TransportRule", "DataLossPrevention"}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Identité d'un message en quarantaine : deux GUID séparés par une barre oblique inverse (ex. 1234…\5678…).
_IDENTITY = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\\[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


@dataclass(frozen=True)
class TraceMessage:
    trace_id: str
    message_id: str
    received: Optional[datetime]
    sender: str
    recipient: str
    subject: str
    status: str            # valeur brute Exchange (Delivered, Failed…)
    size: int
    from_ip: str
    to_ip: str

    @property
    def state(self) -> str:
        return normalize_status(self.status)


@dataclass(frozen=True)
class QuarantinedMessage:
    identity: str
    message_id: str
    received: Optional[datetime]
    expires: Optional[datetime]
    sender: str
    recipients: tuple[str, ...]
    subject: str
    type: str
    release_status: str
    released: bool
    size: int
    direction: str
    policy: str

    @property
    def risky(self) -> bool:
        return self.type in RISKY_TYPES


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _s(row: dict, *keys: str) -> str:
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def _i(row: dict, *keys: str) -> int:
    try:
        return int(_s(row, *keys) or 0)
    except ValueError:
        return 0


def parse_trace(row: dict) -> TraceMessage:
    return TraceMessage(
        trace_id=_s(row, "MessageTraceId", "MessageTraceID"), message_id=_s(row, "MessageId", "MessageID"),
        received=parse_dt(row.get("Received")), sender=_s(row, "SenderAddress").lower(),
        recipient=_s(row, "RecipientAddress").lower(), subject=_s(row, "Subject"), status=_s(row, "Status"),
        size=_i(row, "Size"), from_ip=_s(row, "FromIP", "FromIp"), to_ip=_s(row, "ToIP", "ToIp"),
    )


def parse_quarantine(row: dict) -> QuarantinedMessage:
    rcpt = row.get("RecipientAddress") or []
    recipients = tuple(str(r).lower() for r in ([rcpt] if isinstance(rcpt, str) else rcpt))
    status = _s(row, "ReleaseStatus").upper()
    return QuarantinedMessage(
        identity=_s(row, "Identity"), message_id=_s(row, "MessageId"),
        received=parse_dt(row.get("ReceivedTime")), expires=parse_dt(row.get("Expires")),
        sender=_s(row, "SenderAddress").lower(), recipients=recipients, subject=_s(row, "Subject"),
        type=_s(row, "Type"), release_status=status,
        released=bool(row.get("Released")) or status in ("RELEASED", "APPROVED"),
        size=_i(row, "Size"), direction=_s(row, "Direction"), policy=_s(row, "PolicyName"),
    )


def normalize_status(status: str) -> str:
    return {"delivered": "delivered", "failed": "failed", "quarantined": "quarantined", "filteredasspam": "spam",
            "pending": "pending", "gettingstatus": "pending", "expanded": "expanded", "resolved": "expanded"}.get((status or "").lower(), "other")


def valid_email(value: Optional[str]) -> bool:
    return bool(value) and bool(_EMAIL.match(value.strip()))


def valid_identity(value: Any) -> bool:
    return isinstance(value, str) and bool(_IDENTITY.match(value))


def summarize_trace(messages: Iterable[TraceMessage], start: datetime, end: datetime) -> dict:
    """Totaux par état, volume par heure (pour le graphique) et principaux expéditeurs/destinataires."""
    msgs = list(messages)
    by_state = Counter(m.state for m in msgs)
    first = start.replace(minute=0, second=0, microsecond=0)
    hours = int((end - first).total_seconds() // 3600) + 1
    buckets = {first + timedelta(hours=i): Counter() for i in range(max(hours, 1))}
    for m in msgs:
        if m.received is None:
            continue
        key = m.received.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        if key in buckets:
            buckets[key][m.state] += 1
    return {
        "total": len(msgs),
        "by_state": dict(by_state),
        "per_hour": [{"hour": h.isoformat(), **{k: v for k, v in c.items()}} for h, c in sorted(buckets.items())],
        "top_senders": Counter(m.sender for m in msgs if m.sender).most_common(8),
        "top_recipients": Counter(m.recipient for m in msgs if m.recipient).most_common(8),
        "failure_rate": round(100 * by_state.get("failed", 0) / len(msgs), 1) if msgs else 0.0,
    }


def summarize_quarantine(messages: Iterable[QuarantinedMessage]) -> dict:
    msgs = list(messages)
    return {
        "total": len(msgs),
        "by_type": dict(Counter(m.type or "?" for m in msgs)),
        "pending_release": sum(1 for m in msgs if not m.released),
        "risky": sum(1 for m in msgs if m.risky and not m.released),
    }
