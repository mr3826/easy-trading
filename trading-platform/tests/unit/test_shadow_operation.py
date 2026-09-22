from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from trading_platform.chaos_engine import FAILURE_DUPLICATE_EVENT, FAILURE_STALE_QUOTE, FailureInjector
from trading_platform.data import MarketDataProvider
from trading_platform.domain import Bar, Instrument, OrderSide, OrderType, Signal, TimeInForce
from trading_platform.persistence.shadow_archive import ShadowArchive
from trading_platform.shadow import (
    ACTION_RISK_REJECTED,
    ACTION_WOULD_SUBMIT,
    PROBLEM_DUPLICATE,
    PROBLEM_MISSING,
    PROBLEM_REVISED,
    PROBLEM_STALE,
    SUBMISSION_METHOD_NAMES,
    ShadowBrokerAdapter,
    ShadowOrchestrator,
    ShadowReplayMismatch,
)

UTC = timezone.utc


def make_bar(symbol: str = "AAPL", day: int = 1, opening: float = 100.0) -> Bar:
    instrument = Instrument(symbol)
    return Bar(instrument, datetime(2026, 1, day, tzinfo=UTC), opening, opening + 2, opening - 1, opening + 1, 1000)


class FakeProvider(MarketDataProvider):
    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars

    def get_bars(self, instrument: Instrument, start: datetime, end: datetime, session: Any = None) -> list[Bar]:
        return [bar for bar in self.bars if start <= bar.timestamp <= end]

    def has_bars(self, instrument: Instrument, start: datetime, end: datetime) -> bool:
        return bool(self.get_bars(instrument, start, end))

    def get_latest_bar(self, instrument: Instrument) -> Bar | None:
        return self.bars[-1] if self.bars else None

    def get_metadata(self, instrument: Instrument) -> Any:
        raise NotImplementedError


class FakePolicy:
    def __init__(self, version: int) -> None:
        self.version = version


class FakeRiskEngine:
    def __init__(self, approved: bool = True, reason: str | None = None) -> None:
        self.approved = approved
        self.reason = reason
        self.calls: list[str] = []

    def check_order(self, order: Any, positions: Any, cash: float) -> tuple[bool, str | None, FakePolicy]:
        self.calls.append(f"{order.instrument.symbol}:{order.quantity}")
        if self.approved:
            return True, None, FakePolicy(1)
        return False, self.reason or "rejected by fake risk", FakePolicy(1)


class FlakyStrategy:
    def __init__(self, symbols: set[str] | None = None) -> None:
        self.symbols = symbols or {"AAPL"}
        self.mutated = False

    def __call__(self, bar: Bar) -> Signal | None:
        if self.mutated or bar.instrument.symbol not in self.symbols:
            return None
        return Signal(bar.instrument, OrderSide.BUY, 1, None, OrderType.MARKET, TimeInForce.DAY)


def fixed_now() -> datetime:
    return datetime(2026, 1, 2, tzinfo=UTC)


def build_orchestrator(
    bars: list[Bar], archive_dir: Path, **kwargs: Any
) -> tuple[ShadowOrchestrator, FakeRiskEngine, FlakyStrategy]:
    strategy = FlakyStrategy()
    risk = FakeRiskEngine(**kwargs.pop("risk_kwargs", {}))
    db_sink = kwargs.pop("db_sink", None)
    archive = ShadowArchive(archive_dir, db_sink=db_sink)
    orchestrator = ShadowOrchestrator(FakeProvider(bars), strategy, risk, archive, now_fn=fixed_now, **kwargs)
    return orchestrator, risk, strategy


def read_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_completed_bar_consumption_and_archival(tmp_path: Path) -> None:
    bars = [make_bar(day=1), make_bar(day=2)]
    orchestrator, _risk, _strategy = build_orchestrator(bars, tmp_path / "archive")
    result = orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    assert result.problems == [] and not result.blocked
    assert len(result.decisions) == 2
    inputs = read_lines(tmp_path / "archive" / "inputs.jsonl")
    decisions = read_lines(tmp_path / "archive" / "decisions.jsonl")
    assert len(inputs) == 2 and len(decisions) == 2
    assert all(record["record"]["type"] == "bar" and record["record"]["checksum"] for record in inputs)
    assert all(record["record"]["action"] == ACTION_WOULD_SUBMIT for record in decisions)
    assert decisions[0]["record"]["bar_checksum"] == inputs[0]["record"]["checksum"]
    assert decisions[0]["record"]["symbol"] == "AAPL"
    assert ShadowArchive(tmp_path / "archive").verify()


def test_risk_gated_would_submit(tmp_path: Path) -> None:
    orchestrator, risk, _strategy = build_orchestrator([make_bar(day=1)], tmp_path / "approved")
    result = orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
    assert result.decisions[0]["action"] == ACTION_WOULD_SUBMIT
    assert result.decisions[0]["risk_reason"] is None
    assert risk.calls == ["AAPL:1"]

    rejected, risk, _strategy = build_orchestrator(
        [make_bar(day=1)], tmp_path / "rejected", risk_kwargs={"approved": False, "reason": "position limit"}
    )
    result = rejected.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
    assert result.decisions[0]["action"] == ACTION_RISK_REJECTED
    assert result.decisions[0]["risk_reason"] == "position limit"
    decisions = read_lines(tmp_path / "rejected" / "decisions.jsonl")
    assert all(record["record"]["action"] != ACTION_WOULD_SUBMIT for record in decisions)


def test_strategy_none_produces_no_decision_but_archives_input(tmp_path: Path) -> None:
    orchestrator, _risk, _strategy = build_orchestrator([make_bar(day=1, symbol="MSFT")], tmp_path / "archive")
    result = orchestrator.run(Instrument("MSFT"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
    assert result.decisions == []
    assert len(read_lines(tmp_path / "archive" / "inputs.jsonl")) == 1
    assert not (tmp_path / "archive" / "decisions.jsonl").exists()


def test_structural_submission_impossibility(tmp_path: Path) -> None:
    orchestrator, _risk, _strategy = build_orchestrator([make_bar(day=1)], tmp_path / "archive")
    for name in SUBMISSION_METHOD_NAMES:
        assert not hasattr(orchestrator, name)
    sink = ShadowBrokerAdapter()
    assert not hasattr(sink, "execute_order")
    assert (
        sink.record_would_submit(
            Signal(Instrument("AAPL"), OrderSide.BUY, 1, None, OrderType.MARKET, TimeInForce.DAY), make_bar()
        ).action
        == "WOULD_SUBMIT"
    )


def test_db_sink_protocol_consumption_and_hostile_broker(tmp_path: Path) -> None:
    recorded: list[dict[str, Any]] = []

    class RecordingSink:
        def record_shadow_decision(self, decision: dict[str, Any]) -> None:
            recorded.append(decision)

    class HostileBroker:
        def execute_order(self, *_args: object) -> None:
            raise AssertionError("shadow attempted broker execution")

    class HostileSink(HostileBroker, RecordingSink):
        pass

    orchestrator, _risk, _strategy = build_orchestrator([make_bar(day=1)], tmp_path / "archive", db_sink=HostileSink())
    orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
    assert len(recorded) == 1 and recorded[0]["action"] == ACTION_WOULD_SUBMIT
    assert ShadowArchive(tmp_path / "archive").verify()


def test_stale_data_blocks_decisions(tmp_path: Path) -> None:
    bars = [make_bar(day=1), make_bar(day=9)]
    orchestrator, _risk, _strategy = build_orchestrator(bars, tmp_path / "archive", freshness_limit_seconds=43200.0)
    result = orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 9, tzinfo=UTC))
    stale = [problem for problem in result.problems if problem.problem_type == PROBLEM_STALE]
    assert stale and stale[0].bar_timestamp == datetime(2026, 1, 1, tzinfo=UTC).isoformat()
    assert result.decisions[0]["bar_timestamp"] == datetime(2026, 1, 9, tzinfo=UTC).isoformat()
    assert result.blocked
    problems = read_lines(tmp_path / "archive" / "problems.jsonl")
    assert any(record["record"]["problem_type"] == PROBLEM_STALE for record in problems)


def test_missing_data_blocks_decisions(tmp_path: Path) -> None:
    orchestrator, _risk, _strategy = build_orchestrator([make_bar(day=1)], tmp_path / "archive")
    result = orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    missing = [problem for problem in result.problems if problem.problem_type == PROBLEM_MISSING]
    assert len(missing) == 1
    assert missing[0].bar_timestamp == datetime(2026, 1, 2, tzinfo=UTC).isoformat()
    assert result.decisions[0]["bar_timestamp"] == datetime(2026, 1, 1, tzinfo=UTC).isoformat()
    assert result.blocked
    problems = read_lines(tmp_path / "archive" / "problems.jsonl")
    assert any(record["record"]["problem_type"] == PROBLEM_MISSING for record in problems)


def test_duplicate_data_detection(tmp_path: Path) -> None:
    bars = [make_bar(day=1), make_bar(day=1)]
    orchestrator, _risk, _strategy = build_orchestrator(bars, tmp_path / "archive")
    result = orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
    assert [problem.problem_type for problem in result.problems] == [PROBLEM_DUPLICATE]
    assert len(result.decisions) == 1
    assert len(read_lines(tmp_path / "archive" / "inputs.jsonl")) == 2


def test_duplicate_reconsumption_across_runs(tmp_path: Path) -> None:
    bars = [make_bar(day=1)]
    orchestrator, _risk, _strategy = build_orchestrator(bars, tmp_path / "archive")
    first = orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
    assert first.problems == [] and len(first.decisions) == 1
    second = orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
    assert [problem.problem_type for problem in second.problems] == [PROBLEM_DUPLICATE]
    assert second.decisions == []


def test_revised_data_detection(tmp_path: Path) -> None:
    bars = [make_bar(day=1)]
    orchestrator, _risk, _strategy = build_orchestrator(bars, tmp_path / "archive")
    first = orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
    assert first.problems == []
    orchestrator.provider = FakeProvider([make_bar(day=1, opening=105.0)])
    second = orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
    assert [problem.problem_type for problem in second.problems] == [PROBLEM_REVISED]
    assert second.decisions == []
    problems = read_lines(tmp_path / "archive" / "problems.jsonl")
    assert problems[-1]["record"]["problem_type"] == PROBLEM_REVISED


def test_invalid_bar_is_detected_and_skipped(tmp_path: Path) -> None:
    bars = [make_bar(day=1), make_bar(day=2)]
    orchestrator, _risk, _strategy = build_orchestrator(bars, tmp_path / "archive")
    # Bar construction now rejects invalid economics, so the defensive
    # validation path is exercised by simulating corrupted in-memory state
    object.__setattr__(bars[0], "close", float("nan"))
    result = orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    assert [problem.problem_type for problem in result.problems] == ["invalid"]
    assert result.decisions[0]["bar_timestamp"] == datetime(2026, 1, 2, tzinfo=UTC).isoformat()
    assert result.blocked


def test_replay_matches_archived_decisions(tmp_path: Path) -> None:
    bars = [make_bar(day=1), make_bar(day=2), make_bar(day=3)]
    orchestrator, _risk, _strategy = build_orchestrator(bars, tmp_path / "archive")
    orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 3, tzinfo=UTC))
    outcome = orchestrator.verify_replay()
    assert outcome["match"] is True
    assert outcome["decisions"] == 3 and outcome["discrepancies"] == []


def test_replay_mismatch_persisted_and_escalated(tmp_path: Path) -> None:
    bars = [make_bar(day=1), make_bar(day=2)]
    orchestrator, _risk, strategy = build_orchestrator(bars, tmp_path / "archive")
    orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    strategy.mutated = True
    with pytest.raises(ShadowReplayMismatch):
        orchestrator.verify_replay()
    discrepancies = read_lines(tmp_path / "archive" / "discrepancies.jsonl")
    assert len(discrepancies) == 2
    assert all(record["record"]["kind"] == "missing_in_replay" for record in discrepancies)


def test_replay_is_deterministic_across_fresh_orchestrators(tmp_path: Path) -> None:
    bars = [make_bar(day=1), make_bar(day=2)]
    orchestrator, _risk, _strategy = build_orchestrator(bars, tmp_path / "archive")
    orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    rebuilt, _risk, _strategy = build_orchestrator(bars, tmp_path / "archive")
    assert rebuilt.replay() == orchestrator.replay()
    assert rebuilt.verify_replay()["match"] is True


def test_provider_unavailable_is_recorded_and_blocks(tmp_path: Path) -> None:
    bars = [make_bar(day=1)]
    orchestrator, _risk, _strategy = build_orchestrator(bars, tmp_path / "archive")
    with FailureInjector(FAILURE_STALE_QUOTE) as injector:
        orchestrator.provider.get_bars = injector.wrap(orchestrator.provider.get_bars)
        result = orchestrator.run(
            Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)
        )
    assert any(problem.problem_type == "provider_unavailable" for problem in result.problems)
    assert result.decisions == [] and result.blocked


def test_chaos_duplicate_events_detected_in_shadow(tmp_path: Path) -> None:
    bars = [make_bar(day=1), make_bar(day=2)]
    orchestrator, _risk, _strategy = build_orchestrator(bars, tmp_path / "archive")
    with FailureInjector(FAILURE_DUPLICATE_EVENT) as injector:
        orchestrator.provider.get_bars = injector.wrap(orchestrator.provider.get_bars)
        result = orchestrator.run(
            Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
        )
    duplicates = [problem for problem in result.problems if problem.problem_type == PROBLEM_DUPLICATE]
    assert len(duplicates) == 2
    assert len(result.decisions) == 2
    assert len(read_lines(tmp_path / "archive" / "inputs.jsonl")) == 4
    assert ShadowArchive(tmp_path / "archive").verify()
