"""PaperEvidenceTracker tests: schema validation, aggregation, fail-closed queries."""

from __future__ import annotations

from pathlib import Path

import pytest
from trading_platform.monitoring.paper_evidence import (
    EVENT_FILL,
    EVENT_RISK_VIOLATION,
    EVENT_SESSION,
    EVENT_SIGNAL,
    EVENT_STALE_DATA_TRADE,
    EVENT_UNRESOLVED_RECONCILIATION,
    EvidenceRejected,
    PaperEvidenceTracker,
)


def _tracker(tmp_path: Path) -> PaperEvidenceTracker:
    return PaperEvidenceTracker(tmp_path / "evidence.jsonl")


def _fill(
    tr: PaperEvidenceTracker,
    idx: int,
    symbol: str,
    realized: float,
    modeled: float,
    qty: int = 10,
    modeled_qty: int = 10,
) -> None:
    tr.record(
        EVENT_FILL,
        f"fill-{idx}",
        as_of=f"2026-01-{idx:02d}T15:00:00+00:00",
        symbol=symbol,
        quantity=qty,
        modeled_quantity=modeled_qty,
        modeled_cost=modeled,
        realized_cost=realized,
    )


def _clean_sample(tr: PaperEvidenceTracker, days: int = 70, signals_per_day: int = 1) -> None:
    regimes = ["bullish", "bearish"]
    for d in range(days):
        date = f"2026-{(d // 28) + 1:02d}-{(d % 28) + 1:02d}"
        tr.record(EVENT_SESSION, f"session-{d}", as_of=date, trading_date=date, outcome="CLOSED")
        for s in range(signals_per_day):
            tr.record(
                EVENT_SIGNAL,
                f"sig-{d}-{s}",
                as_of=date,
                symbol=f"SYM{s}",
                regime=regimes[(d + s) % 2],
                strategy_id="trend_rs",
                strategy_version="1.0.0",
            )
            _fill(tr, d * 10 + s, f"SYM{s}", 5.0 + s, 5.0 + s)


def test_malformed_events_rejected(tmp_path: Path) -> None:
    tr = _tracker(tmp_path)
    with pytest.raises(EvidenceRejected):
        tr.record("NOT_A_TYPE", "x", as_of="now")
    with pytest.raises(EvidenceRejected):
        tr.record(EVENT_SESSION, "x", as_of="now", trading_date="01/02/2026", outcome="CLOSED")
    with pytest.raises(EvidenceRejected):
        tr.record(EVENT_SESSION, "x", as_of="now", outcome="CLOSED")  # missing trading_date
    with pytest.raises(EvidenceRejected):
        tr.record(
            EVENT_FILL,
            "x",
            as_of="now",
            symbol="A",
            quantity=-1,
            modeled_cost=1.0,
            realized_cost=1.0,
            modeled_quantity=1,
        )
    with pytest.raises(EvidenceRejected):
        tr.record(EVENT_SIGNAL, "x", as_of="now", symbol="A", regime="bullish", strategy_id="s", strategy_version="")
    assert tr.path.exists() is False or tr.report()["valid_sessions"] == 0


def test_duplicate_event_ids_ignored(tmp_path: Path) -> None:
    tr = _tracker(tmp_path)
    assert tr.record(EVENT_SESSION, "s1", as_of="2026-01-02", trading_date="2026-01-02", outcome="CLOSED")
    assert not tr.record(EVENT_SESSION, "s1", as_of="2026-01-02", trading_date="2026-01-03", outcome="CLOSED")
    assert tr.report()["valid_sessions"] == 1


def test_hard_safety_flags_detected(tmp_path: Path) -> None:
    tr = _tracker(tmp_path)
    _clean_sample(tr, days=5)
    report = tr.report()
    assert report["hard_safety_clean"]
    tr.record(EVENT_RISK_VIOLATION, "rv-1", as_of="2026-01-06", reason="risk engine override attempt")
    tr.record(EVENT_STALE_DATA_TRADE, "st-1", as_of="2026-01-06", reason="bar stale", symbol="SYM0")
    tr.record(EVENT_UNRESOLVED_RECONCILIATION, "ur-1", as_of="2026-01-06", incident_id="inc-9")
    report = tr.report()
    assert not report["hard_safety_clean"]
    assert report["hard_violations"]["risk_violations"] == 1
    assert report["hard_violations"]["stale_data_trades"] == 1
    assert report["hard_violations"]["unresolved_reconciliations"] == 1
    assert report["status"] == "REQUIRES_FORWARD_EVIDENCE"


def test_minimum_sample_requires_days_signals_regimes_and_costs(tmp_path: Path) -> None:
    tr = _tracker(tmp_path)
    _clean_sample(tr, days=61, signals_per_day=1)
    report = tr.report()
    assert report["trading_days"] >= 60
    assert report["executed_signals"] >= 60
    assert len(report["regime_coverage"]) == 2
    assert report["minimum_sample_achieved"] is True
    assert report["status"] == "SAMPLE_MET"

    # too few days
    tr2 = _tracker(tmp_path.parent / "e2.jsonl")
    _clean_sample(tr2, days=30)
    assert tr2.report()["minimum_sample_achieved"] is False

    # single-regime sample does not qualify
    tr3 = _tracker(tmp_path.parent / "e3.jsonl")
    for d in range(61):
        date = f"2026-{(d // 28) + 1:02d}-{(d % 28) + 1:02d}"
        tr3.record(EVENT_SESSION, f"s{d}", as_of=date, trading_date=date, outcome="CLOSED")
        tr3.record(
            EVENT_SIGNAL, f"sig{d}", as_of=date, symbol="A", regime="bullish", strategy_id="s", strategy_version="1.0.0"
        )
        _fill(tr3, d, "A", 5.0, 5.0)
    assert tr3.report()["minimum_sample_achieved"] is False


def test_slippage_and_sizing_deviation_detected(tmp_path: Path) -> None:
    tr = _tracker(tmp_path)
    tr.record(EVENT_SESSION, "s1", as_of="2026-01-02", trading_date="2026-01-02", outcome="CLOSED")
    _fill(tr, 1, "A", realized=10.0, modeled=5.0, qty=10, modeled_qty=10)  # 100% cost error
    _fill(tr, 2, "B", realized=10.0, modeled=10.0, qty=20, modeled_qty=10)  # 100% sizing dev
    report = tr.report()
    assert report["avg_slippage_error"] > 0.05
    assert len(report["sizing_deviations"]) == 1
    assert report["minimum_sample_achieved"] is False


def test_corrupt_line_is_ignored_not_trusted(tmp_path: Path) -> None:
    tr = _tracker(tmp_path)
    tr.record(EVENT_SESSION, "s1", as_of="2026-01-02", trading_date="2026-01-02", outcome="CLOSED")
    with tr.path.open("a", encoding="utf-8") as fh:
        fh.write("{corrupt\n")
    report = tr.report()
    assert report["valid_sessions"] == 1
