"""Shadow execution that can record intent but cannot submit orders.

The shadow orchestrator consumes only completed market bars from an injected
``MarketDataProvider``-like provider, validates every bar (stale, missing,
revised, duplicate, and invalid data are detected, persisted, and skipped),
archives all received inputs, runs production-equivalent strategy and
hard-risk decisions, and persists ``WOULD_SUBMIT`` decisions only when the
strategy submits and hard risk approves. Broker submission is structurally
impossible: no order-submission method exists on the shadow path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from trading_platform.data import validate_bar
from trading_platform.domain import (
    Bar,
    Instrument,
    Order,
    OrderStatus,
    OrderType,
    Signal,
    TimeInForce,
)
from trading_platform.persistence.shadow_archive import (
    ShadowArchive,
    ShadowDecisionSink,
    payload_checksum,
)

PROBLEM_STALE = "stale"
PROBLEM_MISSING = "missing"
PROBLEM_REVISED = "revised"
PROBLEM_DUPLICATE = "duplicate"
PROBLEM_INVALID = "invalid"
PROBLEM_PROVIDER_UNAVAILABLE = "provider_unavailable"

ACTION_WOULD_SUBMIT = "WOULD_SUBMIT"
ACTION_RISK_REJECTED = "RISK_REJECTED"

SUBMISSION_METHOD_NAMES = (
    "execute_order",
    "submit",
    "submit_order",
    "place_order",
    "send_order",
    "place_trade",
)

DEFAULT_FRESHNESS_LIMIT_SECONDS = 86_400.0
DEFAULT_BAR_INTERVAL_SECONDS = 86_400.0


class ShadowReplayMismatch(RuntimeError):
    """Raised when replayed decisions do not exactly match archived ones."""


@dataclass(frozen=True)
class ShadowDecision:
    symbol: str
    action: str
    quantity: int
    bar_timestamp: str


class ShadowBrokerAdapter:
    """A sink-only adapter; submission is structurally unavailable."""

    def __init__(self) -> None:
        self.decisions: list[ShadowDecision] = []

    def record_would_submit(self, signal: Signal, bar: Bar) -> ShadowDecision:
        decision = ShadowDecision(
            signal.instrument.symbol,
            "WOULD_SUBMIT",
            signal.quantity,
            bar.timestamp.isoformat(),
        )
        self.decisions.append(decision)
        return decision


def run_shadow(
    bars: Iterable[Bar],
    strategy: Callable[[Bar], Signal | None],
    sink: ShadowBrokerAdapter,
) -> list[ShadowDecision]:
    """Legacy compatibility helper that records intents only.

    Superseded by :class:`ShadowOrchestrator`, which validates bars, archives
    inputs, applies hard risk, and replays decisions. This helper performs no
    validation or archival and must not be used for shadow operation.
    """
    for bar in bars:
        signal = strategy(bar)
        if signal is not None:
            sink.record_would_submit(signal, bar)
    return list(sink.decisions)


@dataclass(frozen=True)
class DataProblem:
    problem_type: str
    instrument: str
    bar_timestamp: str | None
    detail: str

    def to_record(self) -> dict[str, Any]:
        return {
            "problem_type": self.problem_type,
            "instrument": self.instrument,
            "bar_timestamp": self.bar_timestamp,
            "detail": self.detail,
        }


@dataclass
class ShadowRunResult:
    decisions: list[dict[str, Any]]
    problems: list[DataProblem]
    blocked: bool


def bar_record(bar: Bar) -> dict[str, Any]:
    """Canonical archive record for a received bar, checksummed."""
    record: dict[str, Any] = {
        "type": "bar",
        "symbol": bar.instrument.symbol,
        "timestamp": bar.timestamp.astimezone(timezone.utc).isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }
    record["checksum"] = payload_checksum(record)
    return record


def bar_from_record(record: Mapping[str, Any]) -> Bar:
    """Rebuild a Bar from an archived raw input record."""
    return Bar(
        Instrument(str(record["symbol"])),
        datetime.fromisoformat(str(record["timestamp"])),
        float(record["open"]),
        float(record["high"]),
        float(record["low"]),
        float(record["close"]),
        int(record["volume"]),
    )


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


class ShadowOrchestrator:
    """Consumes completed bars, archives inputs, records risk-gated intents.

    There is deliberately no order-submission method on this class or on the
    shadow path: shadow decisions are recorded and can never reach a broker.
    """

    def __init__(
        self,
        provider: Any,
        strategy: Callable[[Bar], Signal | None],
        risk_engine: Any,
        archive: ShadowArchive,
        *,
        db_sinks: Sequence[ShadowDecisionSink] = (),
        positions: Mapping[str, Any] | None = None,
        cash: float = 10_000.0,
        now_fn: Callable[[], datetime] | None = None,
        freshness_limit_seconds: float = DEFAULT_FRESHNESS_LIMIT_SECONDS,
        bar_interval_seconds: float = DEFAULT_BAR_INTERVAL_SECONDS,
    ) -> None:
        self.provider = provider
        self.strategy = strategy
        self.risk_engine = risk_engine
        self.archive = archive
        self.db_sinks = tuple(db_sinks)
        self.positions: dict[str, Any] = dict(positions) if positions else {}
        self.cash = cash
        self.now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        self.freshness_limit_seconds = freshness_limit_seconds
        self.bar_interval_seconds = bar_interval_seconds
        self.decisions: list[dict[str, Any]] = []
        self.problems: list[DataProblem] = []
        self._seen: dict[tuple[str, str], str] = {}

    def _seed_seen(self) -> None:
        """Mark already-archived inputs as consumed so replays stay dedupable."""
        for record in self.archive.read_inputs():
            if record.get("type") != "bar":
                continue
            key = (str(record.get("symbol", "")), str(record.get("timestamp", "")))
            self._seen.setdefault(key, str(record.get("checksum", "")))

    def _persist_problem(self, problem: DataProblem) -> None:
        self.archive.record_problem(problem.to_record())

    def _record_and_classify(self, bar: Bar, problems: list[DataProblem]) -> DataProblem | None:
        """Archive the received bar and return the problem blocking it, if any."""
        timestamp = bar.timestamp.astimezone(timezone.utc).isoformat()
        record = bar_record(bar)
        self.archive.record_input(record)
        checksum = str(record["checksum"])
        key = (bar.instrument.symbol, timestamp)
        previous = self._seen.get(key)
        if previous is not None:
            problem_type = PROBLEM_DUPLICATE if previous == checksum else PROBLEM_REVISED
            problem = DataProblem(
                problem_type,
                bar.instrument.symbol,
                timestamp,
                f"archived input for this slot has checksum {previous}",
            )
            self._persist_problem(problem)
            problems.append(problem)
            return problem
        self._seen[key] = checksum
        valid, message = validate_bar(bar)
        if not valid:
            problem = DataProblem(PROBLEM_INVALID, bar.instrument.symbol, timestamp, message or "bar failed validation")
            self._persist_problem(problem)
            problems.append(problem)
            return problem
        age = (self.now_fn() - bar.timestamp).total_seconds()
        if age > self.freshness_limit_seconds:
            problem = DataProblem(
                PROBLEM_STALE,
                bar.instrument.symbol,
                timestamp,
                f"bar age {age:.1f}s exceeds freshness limit {self.freshness_limit_seconds}s",
            )
            self._persist_problem(problem)
            problems.append(problem)
            return problem
        return None

    def _missing_slots(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        received: Mapping[str, Bar],
    ) -> list[DataProblem]:
        """Detect expected bar slots for which no completed bar arrived."""
        problems: list[DataProblem] = []
        interval = timedelta(seconds=self.bar_interval_seconds)
        slot = _utc(start, "start")
        end_utc = _utc(end, "end")
        while slot <= end_utc:
            key = slot.isoformat()
            if key not in received:
                problem = DataProblem(
                    PROBLEM_MISSING,
                    instrument.symbol,
                    key,
                    f"no completed bar received for expected slot {key}",
                )
                self._persist_problem(problem)
                problems.append(problem)
            slot += interval
        return problems

    def _decide(self, bar: Bar, record_decision: bool) -> dict[str, Any] | None:
        """Run strategy and hard risk; build the deterministic decision record."""
        signal = self.strategy(bar)
        if signal is None:
            return None
        order = Order(
            order_id=f"shadow_{bar.instrument.symbol}_{bar.timestamp.astimezone(timezone.utc).isoformat()}",
            instrument=bar.instrument,
            side=signal.side,
            quantity=signal.quantity,
            price=bar.close,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
            status=OrderStatus.SUBMITTED,
            signal=signal,
        )
        approved, reason, policy = self.risk_engine.check_order(order, self.positions, self.cash)
        record: dict[str, Any] = {
            "symbol": bar.instrument.symbol,
            "action": ACTION_WOULD_SUBMIT if approved else ACTION_RISK_REJECTED,
            "side": signal.side.name,
            "quantity": signal.quantity,
            "price": bar.close,
            "bar_timestamp": bar.timestamp.astimezone(timezone.utc).isoformat(),
            "bar_close": bar.close,
            "bar_checksum": str(bar_record(bar)["checksum"]),
            "risk_reason": reason,
            "risk_policy_version": policy.version if policy is not None else None,
        }
        if record_decision:
            self.archive.record_shadow_decision(record)
            for sink in self.db_sinks:
                sink.record_shadow_decision(record)
        return record

    def run(self, instrument: Instrument, start: datetime, end: datetime) -> ShadowRunResult:
        """Consume completed bars for the window and record risk-gated decisions."""
        run_problems: list[DataProblem] = []
        run_decisions: list[dict[str, Any]] = []
        self._seed_seen()
        fetched = self.provider.get_bars(instrument, _utc(start, "start"), _utc(end, "end"))
        bars = self._as_bars(fetched, run_problems, instrument)
        received: dict[str, Bar] = {}
        for bar in bars:
            received[bar.timestamp.astimezone(timezone.utc).isoformat()] = bar
            problem = self._record_and_classify(bar, run_problems)
            if problem is not None:
                continue
            decision = self._decide(bar, record_decision=True)
            if decision is not None:
                run_decisions.append(decision)
        run_problems.extend(self._missing_slots(instrument, start, end, received))
        self.problems.extend(run_problems)
        self.decisions.extend(run_decisions)
        return ShadowRunResult(list(run_decisions), list(run_problems), blocked=bool(run_problems))

    def _as_bars(self, fetched: Any, problems: list[DataProblem], instrument: Instrument) -> list[Bar]:
        if fetched is None:
            problem = DataProblem(
                PROBLEM_PROVIDER_UNAVAILABLE,
                instrument.symbol,
                None,
                "provider returned no data window",
            )
            self._persist_problem(problem)
            problems.append(problem)
            return []
        return list(fetched)

    def replay(self) -> list[dict[str, Any]]:
        """Re-run the decision pipeline over archived inputs without writing.

        Deterministic given fixed inputs and a fixed injected clock: the same
        filters (duplicate, revised, invalid, stale) are applied in the same
        order, so replayed decisions match archived decisions exactly.
        """
        seen: dict[tuple[str, str], str] = {}
        replayed: list[dict[str, Any]] = []
        for record in self.archive.read_inputs():
            if record.get("type") != "bar":
                continue
            bar = bar_from_record(record)
            checksum = str(record.get("checksum", ""))
            timestamp = bar.timestamp.astimezone(timezone.utc).isoformat()
            key = (bar.instrument.symbol, timestamp)
            previous = seen.get(key)
            if previous is not None:
                continue
            seen[key] = checksum
            valid, _message = validate_bar(bar)
            if not valid:
                continue
            age = (self.now_fn() - bar.timestamp).total_seconds()
            if age > self.freshness_limit_seconds:
                continue
            decision = self._decide(bar, record_decision=False)
            if decision is not None:
                replayed.append(decision)
        return replayed

    def verify_replay(self) -> dict[str, Any]:
        """Compare replayed decisions against archived ones; escalate mismatches.

        Discrepancies are persisted to the archive and raised as a
        ``ShadowReplayMismatch`` (CRITICAL escalation).
        """
        archived = self.archive.read_decisions()
        replayed = self.replay()
        discrepancies: list[dict[str, Any]] = []
        for index, decision in enumerate(archived):
            if index >= len(replayed):
                discrepancies.append({"kind": "missing_in_replay", "index": index, "decision": decision})
            elif replayed[index] != decision:
                discrepancies.append(
                    {"kind": "decision_mismatch", "index": index, "archived": decision, "replayed": replayed[index]}
                )
        for index in range(len(archived), len(replayed)):
            discrepancies.append({"kind": "extra_in_replay", "index": index, "decision": replayed[index]})
        if discrepancies:
            for item in discrepancies:
                self.archive.record_discrepancy(item)
            raise ShadowReplayMismatch(
                f"replay mismatch: {len(discrepancies)} discrepancies, first={discrepancies[0]['kind']}"
            )
        return {"match": True, "decisions": len(archived), "discrepancies": []}
