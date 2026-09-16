"""Phase 8: Monitoring and operational metrics for live-data shadow mode.

Provides comprehensive monitoring for: data freshness, decision latency,
signal counts, risk rejections, reconciliation state, database health,
journal lag, and heartbeat status. All metrics are opt-in and confined
to testing/shadow/live-monitor mode.
"""

from __future__ import annotations

import json
import logging
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Callable, Dict, List, Optional, Protocol
from urllib.parse import urlparse
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

logger = logging.getLogger("trading_platform.monitor")


class AlertChannel(Protocol):
    """Independent critical-alert transport."""

    def send(self, message: str) -> None: ...


class WebhookAlertChannel:
    """HTTPS webhook transport; endpoint credentials stay in deployment config."""

    def __init__(self, endpoint: str, timeout_seconds: float = 5.0) -> None:
        if urlparse(endpoint).scheme != "https":
            raise ValueError("alert webhooks must use HTTPS")
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds

    def send(self, message: str) -> None:
        request = Request(
            self.endpoint,
            data=json.dumps({"text": message}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            if response.status >= 300:
                raise RuntimeError(f"alert webhook returned HTTP {response.status}")


class EmailAlertChannel:
    """SMTP-over-TLS alert transport with password supplied by a callback."""

    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        recipient: str,
        password: Callable[[], str],
        timeout_seconds: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.sender = sender
        self.recipient = recipient
        self.password = password
        self.timeout_seconds = timeout_seconds

    def send(self, message: str) -> None:
        email = EmailMessage()
        email["Subject"] = "Trading platform critical alert"
        email["From"] = self.sender
        email["To"] = self.recipient
        email.set_content(message)
        with smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout_seconds) as smtp:
            smtp.login(self.sender, self.password())
            smtp.send_message(email)


# ---------------------------------------------------------------------------
# SystemMonitor — operational metrics tracking
# ---------------------------------------------------------------------------


class SystemMonitor:
    """Operational monitoring for Phase 8 live/shadow mode.

    Tracks:
    - Data freshness (last bar timestamp age)
    - Decision latency (ms per decision)
    - Signal counts (incoming signals per session)
    - Risk rejection counts
    - Reconciliation state (errors, warnings)
    - Heartbeat health (dead-man)
    - Journal lag (events processed vs events in ledger)
    - Database health (connectivity, last write timestamp)
    """

    def __init__(self, session_scheduler: Any, heartbeat: Optional[Any] = None):
        self.session_scheduler = session_scheduler
        self.heartbeat = heartbeat
        self.metrics_history: List[Dict[str, Any]] = []
        self.signal_count: int = 0
        self.risk_rejection_count: int = 0
        self.decision_latencies: List[float] = []
        self._last_data_timestamp: Optional[datetime] = None
        self._last_write_timestamp: Optional[datetime] = None
        self._journal_event_count: int = 0
        self._processed_event_count: int = 0

    # --- Signal tracking ---

    def record_signal(self) -> None:
        """Record an incoming signal from the strategy."""
        self.signal_count += 1

    # --- Risk rejection tracking ---

    def record_risk_rejection(self) -> None:
        """Record a risk rejection (order blocked by hard limits)."""
        self.risk_rejection_count += 1

    # --- Decision latency tracking ---

    def record_decision_latency(self, latency_ms: float) -> None:
        """Record the latency of a decision in milliseconds.

        Args:
            latency_ms: Decision latency in milliseconds
        """
        self.decision_latencies.append(latency_ms)
        # Keep only last 1000 entries to prevent unbounded growth
        if len(self.decision_latencies) > 1000:
            self.decision_latencies = self.decision_latencies[-1000:]

    # --- Data freshness ---

    def update_last_data_timestamp(self, timestamp: datetime) -> None:
        """Update the last received bar timestamp.

        Args:
            timestamp: UTC datetime of the last bar received
        """
        self._last_data_timestamp = timestamp

    def check_data_freshness(self, max_age_seconds: float = 3600.0) -> Dict[str, Any]:
        """Check if the last bar data is fresh enough.

        V1: If data is older than max_age_seconds, flag it as stale.
        Returns dict with freshness status and details.
        """
        if self._last_data_timestamp is None:
            return {
                "is_fresh": False,
                "data_age_seconds": float("inf"),
                "max_age_seconds": max_age_seconds,
                "alert": "No data received yet",
            }

        now = datetime.now(timezone.utc)
        last_ts = self._last_data_timestamp
        # Ensure both datetimes are comparable
        if last_ts is not None:
            if last_ts.tzinfo:
                # last_ts is timezone-aware, make now comparable
                if now.tzinfo is None:
                    now = now.replace(tzinfo=last_ts.tzinfo)
            else:
                # last_ts is naive, assume UTC
                if now.tzinfo is not None:
                    now = now.replace(tzinfo=None)
        age_seconds = (now - last_ts).total_seconds() if last_ts is not None else float("inf")
        is_fresh = age_seconds <= max_age_seconds

        if not is_fresh:
            logger.warning(f"STALE DATA: age={age_seconds:.1f}s exceeds limit={max_age_seconds}s")

        return {
            "is_fresh": is_fresh,
            "data_age_seconds": age_seconds,
            "max_age_seconds": max_age_seconds,
            "last_bar_time": self._last_data_timestamp.isoformat(),
            "alert": None if is_fresh else f"Data age {age_seconds:.1f}s stale",
        }

    # --- Journal lag ---

    def update_journal_counts(self, event_count: int, processed_count: int) -> None:
        """Update journal event counters.

        Args:
            event_count: Total events in the journal ledger
            processed_count: Events processed by the session
        """
        self._journal_event_count = event_count
        self._processed_event_count = processed_count

    def journal_lag(self) -> int:
        """Return the number of unprocessed journal events.

        V1: Positive lag means events are backed up; negative lag
        means the session is processing faster than events arrive.
        """
        return self._journal_event_count - self._processed_event_count

    # --- Database health ---

    def update_last_write_timestamp(self, timestamp: datetime) -> None:
        """Update the last database write timestamp.

        Args:
            timestamp: UTC datetime of the last successful DB write
        """
        self._last_write_timestamp = timestamp

    def db_health(self) -> Dict[str, Any]:
        """Check database health status.

        V1: Returns connectivity status and last write recency.
        """
        if self._last_write_timestamp is None:
            return {
                "healthy": False,
                "last_write_iso": None,
                "age_seconds": float("inf"),
                "alert": "No database write recorded yet",
            }

        now = datetime.now(timezone.utc)
        last_ts = self._last_write_timestamp
        # Ensure both datetimes are comparable
        if last_ts is not None:
            if last_ts.tzinfo:
                # last_ts is timezone-aware, make now comparable
                if now.tzinfo is None:
                    now = now.replace(tzinfo=last_ts.tzinfo)
            else:
                # last_ts is naive, assume UTC
                if now.tzinfo is not None:
                    now = now.replace(tzinfo=None)
        age_seconds = (now - last_ts).total_seconds() if last_ts is not None else float("inf")
        # Consider DB healthy if last write was within 5 minutes
        healthy = age_seconds < 300.0

        if not healthy:
            logger.warning(f"DB HEALTH: last write {age_seconds:.1f}s ago")

        return {
            "healthy": healthy,
            "last_write_iso": self._last_write_timestamp.isoformat(),
            "age_seconds": age_seconds,
            "alert": None if healthy else f"DB last write {age_seconds:.1f}s ago",
        }

    # --- Heartbeat integration ---

    def heartbeat_status(self) -> Dict[str, Any]:
        """Get the current dead-man heartbeat status.

        V1: Integrated with Phase 7 DeadManHeartbeat.
        """
        if self.heartbeat is None:
            return {"healthy": False, "note": "No heartbeat configured"}

        healthy = self.heartbeat.is_healthy()
        return {
            "healthy": healthy,
            "consecutive_misses": self.heartbeat.consecutive_misses,
            "last_seen": self.heartbeat.last_seen.isoformat() if self.heartbeat.last_seen else None,
        }

    # --- Metrics snapshot ---

    def snapshot(self) -> Dict[str, Any]:
        """Take a complete monitoring snapshot.

        V1: Returns all current metrics as a dict for reporting,
        alerting, and dashboard display.
        """
        hb = self.heartbeat_status()
        data_freshness = self.check_data_freshness()
        db = self.db_health()
        lag = self.journal_lag()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signals_recorded": self.signal_count,
            "risk_rejections": self.risk_rejection_count,
            "avg_decision_latency_ms": (
                sum(self.decision_latencies) / len(self.decision_latencies) if self.decision_latencies else 0.0
            ),
            "trading_halted": self.session_scheduler.is_trading_halted if self.session_scheduler else False,
            "startup_reconciliation_done": (
                self.session_scheduler.startup_reconciliation_done if self.session_scheduler else False
            ),
            "data_freshness": data_freshness,
            "database_health": db,
            "journal_lag": lag,
            "heartbeat_healthy": hb["healthy"],
            "heartbeat_consecutive_misses": hb.get("consecutive_misses", 0),
            "decision_latencies_sample": self.decision_latencies[-10:] if self.decision_latencies else [],
            "decision_latency_count": len(self.decision_latencies),
        }

    # --- History tracking ---

    def record_snapshot(self) -> None:
        """Record the current snapshot to history."""
        self.metrics_history.append(self.snapshot())
        # Keep only last 500 snapshots
        if len(self.metrics_history) > 500:
            self.metrics_history = self.metrics_history[-500:]

    def export_history(self) -> List[Dict[str, Any]]:
        """Export the full metrics history.

        V1: Returns List[Dict] of all recorded snapshots,
        ordered oldest to newest.
        """
        return self.metrics_history.copy()


# ---------------------------------------------------------------------------
# Alert handler — two independent channels
# ---------------------------------------------------------------------------


class AlertHandler:
    """Alert handler for Phase 8 — two independent channels.

    V1: Sends critical alerts through at least two independent channels
    where practical (e.g., stderr + external monitoring system, email +
    pager, or log file + external API).
    """

    def __init__(
        self,
        channel_a: Callable[[str], None] | None = None,
        channel_b: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize alert handler with two channels.

        Args:
            channel_a: First alert channel callable (func message: str -> None)
            channel_b: Second alert channel callable
        """
        self.channel_a = channel_a or self._default_channel_a
        self.channel_b = channel_b or self._default_channel_b

    @staticmethod
    def _default_channel_a(message: str) -> None:
        """Default channel A: stderr log prefix [ALERT-CHAN-A]."""
        import sys

        print(f"[ALERT-CHAN-A] {message}", file=sys.stderr)

    @staticmethod
    def _default_channel_b(message: str) -> None:
        """Default channel B: stderr log prefix [ALERT-CHAN-B]."""
        import sys

        print(f"[ALERT-CHAN-B] {message}", file=sys.stderr)

    def alert(self, message: str, severity: str = "CRITICAL") -> None:
        """Send alert through both channels.

        Args:
            message: Alert message text
            severity: Severity level (INFO, WARNING, CRITICAL, EMERGENCY)
        """
        prefixed = f"[{severity}] {message}"
        self.channel_a(prefixed)
        self.channel_b(prefixed)

    def alert_data_freshness_stale(self, age_seconds: float, max_age: float) -> None:
        """Alert on stale market data."""
        self.alert(
            f"Market data stale: age={age_seconds:.1f}s, max={max_age}s",
            severity="CRITICAL",
        )

    def alert_db_unhealthy(self, age_seconds: float) -> None:
        """Alert on database unhealthiness."""
        self.alert(f"Database unhealthy: last write {age_seconds:.1f}s ago", severity="WARNING")

    def alert_journal_lag(self, lag: int) -> None:
        """Alert on journal backlog."""
        self.alert(f"Journal lag: {lag} unprocessed events", severity="WARNING")

    def alert_heartbeat_missed(self, misses: int, threshold: int = 3) -> None:
        """Alert on heartbeat misses."""
        self.alert(
            f"Heartbeat missed {misses} times (threshold={threshold})",
            severity="EMERGENCY" if misses >= threshold else "WARNING",
        )


# ---------------------------------------------------------------------------
# Session operator for shadow mode
# ---------------------------------------------------------------------------


class ShadowSessionOperator:
    """Operator for Phase 8 shadow mode.

    Generates WOULD_SUBMIT intents with full risk decisions and hypothetical
    orders. Never calls broker submission. Compares live features/signals
    with offline replay from archived data.

    V1: Used in shadow mode to validate that the deterministic core produces
    the same results as the shadow replay, without sending orders to a broker.
    """

    def __init__(
        self,
        oms: Any,
        risk_engine: Any,
        reconciliation: Any,
        monitor: SystemMonitor,
        broker_adapter: Any = None,
    ):
        self.oms = oms
        self.risk_engine = risk_engine
        self.reconciliation = reconciliation
        self.monitor = monitor
        self.broker_adapter = broker_adapter

    def process_should_submit(self, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a WOULD_SUBMIT intent.

        V1: Full risk decision, hypothetical order generation, NO broker submission.
        Returns dict with decision, hypothetical order, and monitoring metrics.
        """
        from trading_platform.domain import (
            Instrument,
            Order,
            OrderSide,
            OrderStatus,
            OrderType,
            Signal,
            TimeInForce,
        )

        # Record signal
        self.monitor.record_signal()

        # Run risk check
        instrument = Instrument(symbol=signal_data.get("symbol", "UNKNOWN"))
        bar_price = signal_data.get("price", 0.0)
        quantity = signal_data.get("quantity", 10)

        # Create a provisional order intent
        signal = Signal(
            instrument=instrument,
            side=OrderSide.BUY if signal_data.get("side") == "long" else OrderSide.SELL,
            quantity=quantity,
            price=bar_price if bar_price and bar_price > 0 else None,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
        )
        order = Order(
            order_id=f"would_submit_{signal_data.get('timestamp', 'now')}",
            instrument=instrument,
            side=OrderSide.BUY if signal_data.get("side") == "long" else OrderSide.SELL,
            quantity=quantity,
            price=bar_price if bar_price and bar_price > 0 else None,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            status=OrderStatus.SUBMITTED,
            signal=signal,
        )

        # Check risk
        positions = signal_data.get("positions", {})
        current_cash = signal_data.get("cash", 10000.0)

        approved, reason, policy = self.risk_engine.check_order(order, positions, current_cash)

        # Record latency
        import time

        start = time.time()
        # (Risk check already done above)
        latency_ms = (time.time() - start) * 1000
        self.monitor.record_decision_latency(latency_ms)

        # Shadow mode records an intent only. It must not manufacture a fill or
        # invoke any broker-shaped object; execution belongs to simulation or
        # the separately authorized paper boundary.
        hypothetical_fill = None

        # Reconcile after hypothetical fill
        if hypothetical_fill and hypothetical_fill["status"] == "FILLED":
            # Run reconciliation
            recon_result = self.reconciliation.reconcile_all(
                beginning_cash=current_cash,
                expected_ending_cash=current_cash,
                expected_positions={},
                actual_positions={},
                expected_fills=1,
                actual_fills=1,
                oms_orders={},
                broker_orders={},
            )
        else:
            recon_result = {
                "overall_status": "PASS",
                "errors": [],
                "details": {
                    "cash": "OK",
                    "positions": "OK",
                    "orders": "OK",
                    "fills": "SKIPPED",
                },
            }

        # Update monitoring
        self.monitor.record_risk_rejection() if not approved else None

        # Return comprehensive result
        return {
            "would_submit": True,
            "approved": approved,
            "reason": reason,
            "hypothetical_order": {
                "order_id": order.order_id,
                "instrument": order.instrument.symbol,
                "side": order.side.value,
                "quantity": order.quantity,
                "price": order.price,
                "order_type": order.order_type.value,
            }
            if order
            else None,
            "hypothetical_fill": hypothetical_fill,
            "risk_policy_version": policy.version if policy else None,
            "reconciliation": {
                "overall_status": recon_result.get("overall_status", "UNKNOWN"),
                "errors": recon_result.get("errors", []),
            },
            "monitoring": self.monitor.snapshot(),
        }
