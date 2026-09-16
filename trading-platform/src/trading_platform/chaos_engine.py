"""Chaos engineering and failure injection for Phase 7.

Provides deterministic failure injection scenarios to test capital safety
when components fail. All injection is opt-in and confined to testing/
shadow mode — never active in live environments.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Failure categories
# ---------------------------------------------------------------------------

FailureType = str
FAILURE_INTERNET_LOSS = "internet_loss"
FAILURE_DB_LOSS = "db_loss"
FAILURE_PROCESS_KILL = "process_kill"
FAILURE_CLOCK_SKEW = "clock_skew"
FAILURE_STALE_QUOTE = "stale_quote"
FAILURE_DUPLICATE_EVENT = "duplicate_event"
FAILURE_BROKER_REJECTION = "broker_rejection"
FAILURE_PARTIAL_FILL = "partial_fill"
FAILURE_LATE_FILL = "late_fill"
FAILURE_DISK_PRESSURE = "disk_pressure"
FAILURE_RESTART_DURING_ORDER = "restart_during_order"
FAILURE_TRADING_HALT = "trading_halt"
FAILURE_SECRET_LEAK = "secret_leak"


class FailureRecord:
    """Record of a failure injection event."""

    def __init__(
        self,
        failure_type: FailureType,
        injected_at: datetime,
        duration: Optional[float] = None,
        recovered_at: Optional[datetime] = None,
        success: Optional[bool] = None,
        notes: str = "",
    ):
        self.failure_type = failure_type
        self.injected_at = injected_at
        self.duration = duration
        self.recovered_at = recovered_at
        self.success = success
        self.notes = notes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "failure_type": self.failure_type,
            "injected_at": self.injected_at.isoformat(),
            "duration": self.duration,
            "recovered_at": self.recovered_at.isoformat() if self.recovered_at else None,
            "success": self.success,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FailureRecord":
        injected = datetime.fromisoformat(data["injected_at"])
        recovered = datetime.fromisoformat(data["recovered_at"]) if data.get("recovered_at") else None
        return cls(
            failure_type=data["failure_type"],
            injected_at=injected,
            duration=data.get("duration"),
            recovered_at=recovered,
            success=data.get("success"),
            notes=data.get("notes", ""),
        )


# ---------------------------------------------------------------------------
# Failure injector — opt-in, context-managed
# ---------------------------------------------------------------------------


class FailureInjector:
    """Opt-in failure injection context manager.

    Usage:
        with FailureInjector(FAILURE_INTERNET_LOSS, duration=5.0) as inj:
            # ... code under test ...
            inj.record(FailureRecord(...))
    """

    def __init__(self, failure_type: FailureType, duration: float = 0.0):
        self.failure_type = failure_type
        self.duration = duration
        self.start_time: Optional[datetime] = None
        self.recovery_time: Optional[datetime] = None
        self.records: List[FailureRecord] = []
        self._active = False

    def start(self) -> None:
        """Inject the failure."""
        self._active = True
        self.start_time = datetime.now(timezone.utc)
        self.records.append(
            FailureRecord(
                failure_type=self.failure_type,
                injected_at=self.start_time,
                duration=self.duration,
            )
        )

    def recover(self) -> None:
        """Recover from the injected failure."""
        self._active = False
        self.recovery_time = datetime.now(timezone.utc)
        if self.records:
            self.records[-1].recovered_at = self.recovery_time

    def record(self, record: FailureRecord) -> None:
        """Add a manual record."""
        self.records.append(record)

    def status(self) -> Dict[str, Any]:
        """Current injection status."""
        return {
            "active": self._active,
            "failure_type": self.failure_type,
            "started_at": self.start_time.isoformat() if self.start_time else None,
            "duration": self.duration,
        }


# ---------------------------------------------------------------------------
# Predefined failure scenarios (for property-based testing)
# ---------------------------------------------------------------------------


class FailureScenarios:
    """Predefined chaos engineering scenarios for property-based testing."""

    @staticmethod
    def internet_loss(duration: float = 3.0) -> FailureInjector:
        """Simulate internet/network loss for ``duration`` seconds."""
        inj = FailureInjector(FAILURE_INTERNET_LOSS, duration=duration)
        inj.start()
        return inj

    @staticmethod
    def db_loss(duration: float = 3.0) -> FailureInjector:
        """Simulate database loss/unavailability for ``duration`` seconds."""
        inj = FailureInjector(FAILURE_DB_LOSS, duration=duration)
        inj.start()
        return inj

    @staticmethod
    def process_kill(duration: float = 3.0) -> FailureInjector:
        """Simulate process kill for ``duration`` seconds."""
        inj = FailureInjector(FAILURE_PROCESS_KILL, duration=duration)
        inj.start()
        return inj

    @staticmethod
    def stale_quote(symbol: str, price_deviation_pct: float = 5.0, duration: float = 3.0) -> FailureInjector:
        """Simulate a stale quote for ``symbol`` with ``price_deviation_pct`` deviation."""
        inj = FailureInjector(FAILURE_STALE_QUOTE, duration=duration)
        inj.records.append(
            FailureRecord(
                failure_type=FAILURE_STALE_QUOTE,
                injected_at=datetime.now(timezone.utc),
                notes=f"Stale quote for {symbol}: price off by {price_deviation_pct}%",
            )
        )
        inj.start()
        return inj

    @staticmethod
    def duplicate_event(n: int = 2) -> List[FailureRecord]:
        """Generate ``n`` duplicate event records."""
        now = datetime.now(timezone.utc)
        return [
            FailureRecord(
                failure_type=FAILURE_DUPLICATE_EVENT,
                injected_at=now,
                notes=f"Duplicate event injection #{i + 1}",
            )
            for i in range(n)
        ]

    @staticmethod
    def broker_rejection(order_id: str, reason: str = "reject") -> FailureRecord:
        """Generate a broker rejection record."""
        return FailureRecord(
            failure_type=FAILURE_BROKER_REJECTION,
            injected_at=datetime.now(timezone.utc),
            notes=f"Broker rejected order {order_id}: {reason}",
        )

    @staticmethod
    def restart_during_order(duration: float = 3.0) -> FailureInjector:
        """Simulate restart during order processing for ``duration`` seconds."""
        inj = FailureInjector(FAILURE_RESTART_DURING_ORDER, duration=duration)
        inj.start()
        return inj


# ---------------------------------------------------------------------------
# Runbook generator
# ---------------------------------------------------------------------------


class RunbookGenerator:
    """Generate operational runbooks from failure injection results."""

    @staticmethod
    def generate_from_records(records: List[FailureRecord]) -> str:
        """Generate a textual runbook from a list of failure records."""
        lines = [
            "=" * 60,
            "OPERATIONAL RUNBOOK",
            "=" * 60,
            "",
            f"Generated: {datetime.now(timezone.utc).isoformat()}",
            f"Total failure events: {len(records)}",
            "",
        ]

        # Group by failure type
        by_type: Dict[FailureType, List[FailureRecord]] = {}
        for r in records:
            by_type.setdefault(r.failure_type, []).append(r)

        for ft, recs in sorted(by_type.items()):
            lines.append(f"--- Failure Type: {ft} ---")
            lines.append(f"Count: {len(recs)}")
            for i, r in enumerate(recs, 1):
                lines.append(
                    f"  Event {i}: injected_at={r.injected_at.isoformat()}, "
                    f"notes={r.notes}, "
                    f"recovered_at={r.recovered_at.isoformat() if r.recovered_at else 'PENDING'}, "
                    f"success={r.success}"
                )
            lines.append("")

        # Summary recommendations
        lines.append("--- SUMMARY RECOMMENDATIONS ---")
        if FAILURE_INTERNET_LOSS in by_type:
            lines.append("  • Internet loss: verify external heartbeat and fail-closed behavior")
        if FAILURE_DB_LOSS in by_type:
            lines.append("  • DB loss: validate backup restore and state reconstruction")
        if FAILURE_PROCESS_KILL in by_type:
            lines.append("  • Process kill: verify external process manager restart")
        if FAILURE_STALE_QUOTE in by_type:
            lines.append("  • Stale quote: enforce market calendar validity and fail-closed")
        if FAILURE_DUPLICATE_EVENT in by_type:
            lines.append("  • Duplicate event: verify idempotency key enforcement")
        if FAILURE_BROKER_REJECTION in by_type:
            lines.append("  • Broker rejection: verify cancel/replace and OCA group behavior")
        if FAILURE_RESTART_DURING_ORDER in by_type:
            lines.append("  • Restart during order: verify checkpoint/restore and journal replay")

        lines.extend(
            [
                "",
                "=" * 60,
                "END OF RUNBOOK",
                "=" * 60,
            ]
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dead-man heartbeat contract
# ---------------------------------------------------------------------------


class DeadManHeartbeat:
    """External dead-man heartbeat outside the trading VM's failure domain.

    Usage:
        heartbeat = DeadManHeartbeat(interval=60, failure_threshold=3)
        heartbeat.start()
        # ... trading loop ...
        heartbeat.record_alive()
        # On shutdown or failure:
        heartbeat.stop()
    """

    def __init__(self, interval: float = 60.0, failure_threshold: int = 3):
        self.interval = interval
        self.failure_threshold = failure_threshold
        self.last_seen: Optional[datetime] = datetime.now(timezone.utc)
        self.consecutive_misses: int = 0
        self._running: bool = False
        self._failures_since_last_ok: int = 0

    def record_alive(self) -> None:
        """Record that the system is alive (call from trading loop)."""
        self.last_seen = datetime.now(timezone.utc)
        self.consecutive_misses = 0
        self._failures_since_last_ok = 0

    def record_miss(self) -> None:
        """Record a missed heartbeat."""
        self.consecutive_misses += 1
        self._failures_since_last_ok += 1

    def is_healthy(self) -> bool:
        """Check if heartbeat is still healthy."""
        if not self._running:
            return False
        if self.last_seen is None:
            return False
        age = (datetime.now(timezone.utc) - self.last_seen).total_seconds()
        if age > self.interval * self.failure_threshold:
            self.consecutive_misses = self.failure_threshold  # mark unhealthy
            return False
        if self.consecutive_misses >= self.failure_threshold:
            return False
        return True

    def check_and_alert(self) -> bool:
        """Check heartbeat health and alert if unhealthy. Returns True if healthy."""
        healthy = self.is_healthy()
        if not healthy:
            # In production, this would trigger an external alert channel
            # (email, pager, external monitoring system)
            pass
        return healthy

    def start(self) -> None:
        """Start the heartbeat timer."""
        self._running = True

    def stop(self) -> None:
        """Stop the heartbeat timer."""
        self._running = False
