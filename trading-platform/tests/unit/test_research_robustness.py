"""Research robustness tests: as-of no-lookahead, parameter stability, cost
sensitivity, bootstrap on trade-level equity, determinism verification,
malformed record rejection, chronological isolation, and provenance hashes."""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from trading_platform.persistence.baseline_report import compute_turnover
from trading_platform.persistence.experiment import (
    EXPERIMENTS_ROOT,
    ExperimentRecord,
    ExperimentRegistry,
    MalformedExperimentRecord,
    experiments_root,
    validate_provenance_hash,
)
from trading_platform.simulator.event_driven_simulator import EventDrivenSimulator
from trading_platform.strategies.ma_cross_strategy import MaCrossHypothesis, generate_signal
from trading_platform.walk_forward.walk_forward import PeriodSplit, WalkForwardEvaluator

UTC = timezone.utc


def _synthetic_bars(days: int = 60) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Deterministic synthetic daily bars for one symbol."""
    bars_by_date: dict[str, list[dict[str, Any]]] = {}
    start = datetime(2026, 1, 1, tzinfo=UTC)
    for day in range(days):
        close = 100.0 + (day % 10) + (day // 10) * 0.5
        timestamp = start + timedelta(days=day)
        bars_by_date[timestamp.date().isoformat()] = [
            {
                "timestamp": timestamp,
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1000 + day,
            }
        ]
    return {"SYNTH": bars_by_date}


def _make_fold() -> dict[str, tuple[date, date]]:
    return PeriodSplit(30, 10, 15).split(date(2026, 1, 1), date(2026, 3, 31))


def _make_evaluator(root: Path) -> WalkForwardEvaluator:
    return WalkForwardEvaluator(
        EventDrivenSimulator(start_cash=10000.0, seed=42),
        MaCrossHypothesis(),
        PeriodSplit(30, 10, 15),
        ExperimentRegistry(root=root / "experiments"),
    )


# ---------------------------------------------------------------------------
# As-of no-lookahead signal generation


def test_generate_signal_as_of_excludes_future_bars() -> None:
    """A decision at Jan 5 must not consider the strongly rising bars of Jan 6-8."""
    hypothesis = MaCrossHypothesis(fast_length=2, slow_length=3)
    declining = [{"timestamp": datetime(2026, 1, day, tzinfo=UTC), "close": float(10 - day)} for day in range(1, 6)]
    rising = [{"timestamp": datetime(2026, 1, day, tzinfo=UTC), "close": float(50 + day)} for day in range(6, 9)]
    bars = {"AAPL": declining + rising}

    as_of_jan5 = datetime(2026, 1, 5, tzinfo=UTC)
    assert generate_signal(bars, hypothesis, "AAPL", date(2026, 1, 5), as_of=as_of_jan5) is None

    as_of_jan8 = datetime(2026, 1, 8, tzinfo=UTC)
    signal = generate_signal(bars, hypothesis, "AAPL", date(2026, 1, 8), as_of=as_of_jan8)
    assert signal is not None and signal["side"] == "BUY"


def test_generate_signal_uses_completed_decision_date_bar() -> None:
    """The SMA window includes the completed bar for the decision date."""
    hypothesis = MaCrossHypothesis(fast_length=2, slow_length=3)
    bars = {"AAPL": [{"timestamp": datetime(2026, 1, day, tzinfo=UTC), "close": float(day)} for day in range(1, 7)]}
    signal = generate_signal(bars, hypothesis, "AAPL", date(2026, 1, 6))
    assert signal is not None and signal["side"] == "BUY"


def test_generate_signal_exit_on_downtrend_and_holding_window() -> None:
    """Long-only exits: SELL when fast crosses below slow or max holding reached."""
    hypothesis = MaCrossHypothesis(fast_length=2, slow_length=3, max_holding_days=30)
    bars = {
        "AAPL": [{"timestamp": datetime(2026, 1, day, tzinfo=UTC), "close": float(10 - day)} for day in range(1, 6)]
    }
    as_of = datetime(2026, 1, 5, tzinfo=UTC)
    held = {"quantity": 5, "days_held": 0}
    signal = generate_signal(bars, hypothesis, "AAPL", date(2026, 1, 5), as_of=as_of, position_held=held)
    assert signal is not None and signal["side"] == "SELL"

    # Still in an uptrend and inside the holding window: hold (no signal)
    rising = {"AAPL": [{"timestamp": datetime(2026, 1, day, tzinfo=UTC), "close": float(day)} for day in range(1, 6)]}
    assert generate_signal(rising, hypothesis, "AAPL", date(2026, 1, 5), as_of=as_of, position_held=held) is None

    # Holding window exceeded: SELL even in an uptrend
    expired = {"quantity": 5, "days_held": 30}
    held_too_long = generate_signal(rising, hypothesis, "AAPL", date(2026, 1, 5), as_of=as_of, position_held=expired)
    assert held_too_long is not None and held_too_long["side"] == "SELL"


def test_generate_signal_never_shorts() -> None:
    """SELL signals only occur when a position is held; no SELL while flat."""
    hypothesis = MaCrossHypothesis(fast_length=2, slow_length=3)
    bars = {
        "AAPL": [{"timestamp": datetime(2026, 1, day, tzinfo=UTC), "close": float(10 - day)} for day in range(1, 6)]
    }
    for day in range(1, 6):
        signal = generate_signal(bars, hypothesis, "AAPL", date(2026, 1, day), as_of=datetime(2026, 1, day, tzinfo=UTC))
        if signal is not None:
            assert signal["side"] == "BUY"


# ---------------------------------------------------------------------------
# Walk-forward: isolation, determinism, long-only


def test_chronological_isolation_train_never_sees_val_test(tmp_path: Path) -> None:
    """Poisoning validation/test bars must not change train metrics."""
    bars = _synthetic_bars()
    fold = _make_fold()
    clean = _make_evaluator(tmp_path / "clean")
    clean_result = clean.run_fold(0, fold, bars, ["SYNTH"])

    val_start, val_end = fold["validation"]
    test_start, test_end = fold["test"]
    poisoned = copy.deepcopy(bars)
    for date_str, bar_list in poisoned["SYNTH"].items():
        d = date.fromisoformat(date_str)
        if val_start <= d <= val_end or test_start <= d <= test_end:
            for bar in bar_list:
                bar["close"] = 999999.0
                bar["open"] = 999999.0
                bar["high"] = 999999.0
                bar["low"] = 999999.0
    poison_result = _make_evaluator(tmp_path / "poisoned").run_fold(0, fold, poisoned, ["SYNTH"])

    assert poison_result["train_final_cash"] == clean_result["train_final_cash"]
    assert poison_result["train_total_pnl"] == clean_result["train_total_pnl"]
    assert poison_result["train_trade_count"] == clean_result["train_trade_count"]
    assert (
        poison_result["test_total_pnl"] != clean_result["test_total_pnl"]
        or poison_result["test_final_cash"] != clean_result["test_final_cash"]
    )


def test_fold_determinism_is_verified_by_rerun(tmp_path: Path) -> None:
    """Every phase is verified by re-running it and comparing ledgers."""
    evaluator = _make_evaluator(tmp_path)
    result = evaluator.run_fold(0, _make_fold(), _synthetic_bars(), ["SYNTH"])
    assert result["train_deterministic"] is True
    assert result["val_deterministic"] is True
    assert result["test_deterministic"] is True
    assert result["long_only_preserved"] is True


def test_walk_forward_fold_over_real_synthetic_data(tmp_path: Path) -> None:
    """A fold over synthetic data produces real, consistent metrics."""
    evaluator = _make_evaluator(tmp_path)
    result = evaluator.run_fold(0, _make_fold(), _synthetic_bars(), ["SYNTH"])
    assert result["test_final_cash"] > 0
    assert result["test_trade_count"] >= 0
    assert result["test_total_commission"] >= 0
    assert isinstance(result["test_max_drawdown"], float)
    assert isinstance(result["test_turnover"], float)
    assert result["hypothesis_id"] == "ma_cross_5_20"


# ---------------------------------------------------------------------------
# Robustness analyses


def test_parameter_stability_analysis_reports_dispersion(tmp_path: Path) -> None:
    bars = _synthetic_bars()
    evaluator = _make_evaluator(tmp_path)
    folds = PeriodSplit(30, 10, 15).walk_forward(date(2026, 1, 1), date(2026, 3, 31), step_forward=15)
    result = evaluator.parameter_stability_analysis(bars, ["SYNTH"], folds[:2])
    assert len(result["variants"]) == 2
    assert "variant_test_pnl_spread" in result
    assert "base_test_pnls" in result
    assert "never tuned toward profitability" in result["note"]


def test_cost_sensitivity_analysis_reports_deltas(tmp_path: Path) -> None:
    bars = _synthetic_bars()
    evaluator = _make_evaluator(tmp_path)
    folds = PeriodSplit(30, 10, 15).walk_forward(date(2026, 1, 1), date(2026, 3, 31), step_forward=15)
    result = evaluator.cost_sensitivity_analysis(bars, ["SYNTH"], folds[:2])
    assert "base_test_pnl" in result
    assert len(result["scenarios"]) == 6  # 3 commission x 2 slippage multipliers
    assert all("delta_vs_base" in scenario for scenario in result["scenarios"])


def test_bootstrap_drawdown_is_trade_level_and_seed_reproducible(tmp_path: Path) -> None:
    evaluator = _make_evaluator(tmp_path)
    evaluator.fold_results.append(
        {
            "test_total_pnl": 10.0,
            "test_total_commission": 1.0,
            "test_total_slippage": 0.0,
            "test_trade_pnls": [5.0, -2.0, 8.0, -1.0],
            "test_max_drawdown": 0.01,
            "test_turnover": 0.001,
            "test_gross_exposure_pct": 10.0,
            "long_only_preserved": True,
        }
    )
    first = evaluator.bootstrap_drawdown_distribution(n_resamples=200, seed=7)
    second = evaluator.bootstrap_drawdown_distribution(n_resamples=200, seed=7)
    assert first == second
    assert first["max"] >= first["p95"] >= first["p5"] >= first["min"] >= 0.0
    assert first["seed"] == 7


def test_bootstrap_requires_fold_results(tmp_path: Path) -> None:
    evaluator = _make_evaluator(tmp_path)
    assert evaluator.bootstrap_drawdown_distribution() == {"error": "No fold results recorded"}


# ---------------------------------------------------------------------------
# Experiment provenance: real hashes, malformed rejection, platform root


def test_validate_provenance_hash_rejects_placeholders_and_truncation() -> None:
    with pytest.raises(MalformedExperimentRecord):
        validate_provenance_hash("placeholder_code_commit_hash", "code")
    with pytest.raises(MalformedExperimentRecord):
        validate_provenance_hash("", "code")
    with pytest.raises(MalformedExperimentRecord):
        validate_provenance_hash("abc123", "code")
    assert validate_provenance_hash("a" * 40, "code") == "a" * 40
    assert validate_provenance_hash("b" * 64, "code") == "b" * 64
    assert validate_provenance_hash("dep-lock-123", "code") == "dep-lock-123"


def test_compute_code_hash_is_a_real_git_sha() -> None:
    code_hash = ExperimentRecord.compute_code_hash()
    assert len(code_hash) in (40, 64)


def test_compute_code_hash_env_fallback_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_PLATFORM_CODE_SHA", "c" * 64)
    assert ExperimentRecord.compute_code_hash() == "c" * 64
    monkeypatch.setenv("TRADING_PLATFORM_CODE_SHA", "abc123")
    with pytest.raises(MalformedExperimentRecord):
        ExperimentRecord.compute_code_hash()


def test_compute_dependency_lock_hash_is_sha256_of_lock_bytes() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    lock_hash = ExperimentRecord.compute_dependency_lock_hash(repo_root / "uv.lock")
    assert len(lock_hash) == 64


def test_compute_policy_hash_is_deterministic() -> None:
    policy = {"max_positions": 3, "version": "0.1-PROVISIONAL"}
    assert ExperimentRecord.compute_policy_hash(policy) == ExperimentRecord.compute_policy_hash(policy)
    assert len(ExperimentRecord.compute_policy_hash(policy)) == 64


def test_registry_rejects_malformed_records(tmp_path: Path) -> None:
    root = tmp_path / "experiments"
    bad_dir = root / "bad-exp"
    bad_dir.mkdir(parents=True)
    (bad_dir / "experiment.json").write_text("{not valid json")
    with pytest.raises(MalformedExperimentRecord):
        ExperimentRegistry(root=root)

    (bad_dir / "experiment.json").write_text('{"experiment_id": "x"}')
    with pytest.raises(MalformedExperimentRecord):
        ExperimentRegistry(root=root)


def test_registry_accepts_valid_records_and_reports_them(tmp_path: Path) -> None:
    registry = ExperimentRegistry(root=tmp_path / "experiments")
    record = ExperimentRecord(
        experiment_id=ExperimentRecord.new_experiment_id(),
        hypothesis=MaCrossHypothesis(),
        dataset_hash=ExperimentRecord.compute_dataset_hash("data"),
        universe_version="v1.0",
        code_commit=ExperimentRecord.compute_code_hash(),
        dependency_lock=ExperimentRecord.compute_dependency_lock_hash(Path(__file__).resolve().parents[3] / "uv.lock"),
        cost_slippage_assumptions={"commission": 1.0},
        seed=42,
    )
    registry.register(record)
    assert registry.count() == 1
    assert registry.get(record.experiment_id) is record


def test_experiments_root_is_platform_appropriate() -> None:
    assert EXPERIMENTS_ROOT.is_absolute()
    assert "trading_experiments" in str(EXPERIMENTS_ROOT)
    assert experiments_root().is_absolute()


def test_experiments_root_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADING_EXPERIMENTS_ROOT", str(tmp_path / "custom"))
    assert experiments_root() == tmp_path / "custom"


# ---------------------------------------------------------------------------
# Turnover with equity base


def test_compute_turnover_with_equity_base() -> None:
    assert compute_turnover(3, 2) == 5.0
    assert compute_turnover(3, 2, equity=100.0) == 0.05
    assert compute_turnover(3, 2, equity=0.0) == 5.0
