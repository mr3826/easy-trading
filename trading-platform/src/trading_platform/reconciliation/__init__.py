"""Independent state reconciliation primitives.

Fail-closed chain: MISMATCH -> BLOCK_NEW_ORDERS -> PERSIST_INCIDENT ->
CRITICAL_ALERT -> OPERATOR_RESOLUTION_REQUIRED. New orders stay blocked until
an operator resolution is recorded; the block survives persistence or alert
delivery failures.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence

logger = logging.getLogger("trading_platform.reconciliation")

OPERATOR_RESOLUTION_REQUIRED = "OPERATOR_RESOLUTION_REQUIRED"


@dataclass(frozen=True)
class ReconciliationReport:
    matched: bool
    differences: tuple[str, ...]
    blocks_new_orders: bool


def reconcile_positions(local: Mapping[str, float], broker: Mapping[str, float]) -> ReconciliationReport:
    """Compare independently-derived positions and fail closed."""
    symbols = sorted(set(local) | set(broker))
    differences = tuple(
        f"{symbol}: local={local.get(symbol, 0)} broker={broker.get(symbol, 0)}"
        for symbol in symbols
        if local.get(symbol, 0) != broker.get(symbol, 0)
    )
    return ReconciliationReport(not differences, differences, bool(differences))


@dataclass
class Incident:
    """Safety incident recorded on a reconciliation mismatch."""

    incident_id: str
    severity: str
    status: str
    differences: tuple[str, ...]
    operator_resolution_required: bool
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None

    def to_details(self) -> dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "severity": self.severity,
            "status": self.status,
            "differences": list(self.differences),
            "operator_resolution_required": self.operator_resolution_required,
            "created_at": self.created_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolved_by": self.resolved_by,
        }


class IncidentSink(Protocol):
    """Injectable incident-persistence seam (contracts C2/C4)."""

    def record_incident(self, incident: Mapping[str, Any]) -> None: ...


class QueuedIncidentSink:
    """Thread-safe in-memory incident sink for tests and local runs."""

    def __init__(self) -> None:
        self._incidents: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record_incident(self, incident: Mapping[str, Any]) -> None:
        with self._lock:
            self._incidents.append(dict(incident))

    def recorded(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._incidents)


class PostgresIncidentSink:
    """Bridge the synchronous reconciliation path to an async PostgresStore."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._background_tasks: set[asyncio.Task[None]] = set()

    def record_incident(self, incident: Mapping[str, Any]) -> None:
        details = incident.get("details", {})
        created_at = incident.get("created_at")
        coroutine = self._store.record_incident(
            str(incident["incident_id"]),
            str(incident.get("severity", "CRITICAL")),
            dict(details),
            created_at if isinstance(created_at, datetime) else datetime.now(timezone.utc),
        )
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coroutine)
            return
        task = asyncio.get_running_loop().create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)


class FailClosedChain:
    """Coordinates the fail-closed chain on a confirmed reconciliation mismatch."""

    def __init__(
        self,
        incident_sink: IncidentSink | None = None,
        alert_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.incident_sink = incident_sink
        self.alert_callback = alert_callback
        self.blocks_new_orders = False
        self.status = "CLEARED"
        self.incidents: list[Incident] = []

    def trigger(self, differences: Sequence[str], source: str) -> Incident:
        self.blocks_new_orders = True
        self.status = OPERATOR_RESOLUTION_REQUIRED
        incident = Incident(
            incident_id=f"incident-{uuid.uuid4()}",
            severity="CRITICAL",
            status=OPERATOR_RESOLUTION_REQUIRED,
            differences=tuple(differences),
            operator_resolution_required=True,
            created_at=datetime.now(timezone.utc),
        )
        self.incidents.append(incident)
        self._persist(incident, source)
        self._alert(incident, source)
        return incident

    def _persist(self, incident: Incident, source: str) -> None:
        if self.incident_sink is None:
            logger.error(
                "PERSIST_INCIDENT unavailable for %s: no incident sink configured; new orders remain blocked",
                source,
            )
            return
        payload = {
            "incident_id": incident.incident_id,
            "severity": incident.severity,
            "status": incident.status,
            "operator_resolution_required": incident.operator_resolution_required,
            "differences": list(incident.differences),
            "created_at": incident.created_at,
            "details": {**incident.to_details(), "source": source},
        }
        try:
            self.incident_sink.record_incident(payload)
        except Exception:
            logger.exception("PERSIST_INCIDENT failed for %s; new orders remain blocked", source)

    def _alert(self, incident: Incident, source: str) -> None:
        message = (
            f"[CRITICAL] reconciliation mismatch ({source}): {'; '.join(incident.differences)} "
            f"- {OPERATOR_RESOLUTION_REQUIRED}"
        )
        logger.critical(message)
        if self.alert_callback is None:
            return
        try:
            self.alert_callback(message)
        except Exception:
            logger.exception("CRITICAL_ALERT delivery failed for %s; new orders remain blocked", source)

    def resolve(self, operated_by: str, notes: str = "") -> bool:
        """Record an operator resolution; the only path that unblocks new orders."""
        if not operated_by:
            return False
        unresolved = [incident for incident in self.incidents if incident.resolved_at is None]
        if not unresolved:
            return False
        resolved_at = datetime.now(timezone.utc)
        for incident in unresolved:
            incident.resolved_at = resolved_at
            incident.resolved_by = operated_by
            incident.status = "RESOLVED"
        self.blocks_new_orders = False
        self.status = "RESOLVED"
        logger.info("OPERATOR_RESOLUTION recorded by %s: %s", operated_by, notes)
        return True

    def unresolved_incidents(self) -> tuple[Incident, ...]:
        return tuple(incident for incident in self.incidents if incident.resolved_at is None)
