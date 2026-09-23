"""First-fold walk-forward exercise.

Runs the first walk-forward fold over a deterministic synthetic dataset and
prints the fold result. This is a PLATFORM correctness exercise: it verifies
chronological splits, point-in-time signal generation and deterministic
simulation. It produces NO strategy or broker evidence and makes no
profitability claim.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from trading_platform.persistence.experiment import ExperimentRegistry
from trading_platform.simulator.event_driven_simulator import EventDrivenSimulator
from trading_platform.strategies.ma_cross_strategy import MaCrossHypothesis
from trading_platform.walk_forward.walk_forward import PeriodSplit, WalkForwardEvaluator


def _synthetic_bars(days: int = 60) -> dict[str, dict[str, list[dict[str, object]]]]:
    """Deterministic synthetic daily bars for one symbol (platform test data)."""
    bars_by_date: dict[str, list[dict[str, object]]] = {}
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
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


def main() -> None:
    bars_by_symbol = _synthetic_bars()
    period_split = PeriodSplit(train_days=30, validation_days=10, test_days=15)
    evaluator = WalkForwardEvaluator(
        EventDrivenSimulator(start_cash=10000.0, seed=42),
        MaCrossHypothesis(),
        period_split,
        ExperimentRegistry(root=Path(__file__).resolve().parent / ".walk_forward_first_fold_experiments"),
    )

    folds = period_split.walk_forward(datetime(2026, 1, 1).date(), datetime(2026, 3, 1).date(), step_forward=15)
    if not folds:
        print("walk-forward first fold: no folds generated for the requested range")
        return

    first_fold = folds[0]
    fold_result = evaluator.run_fold(0, first_fold, bars_by_symbol, ["SYNTH"])

    print("walk-forward first fold completed")
    print(f"  fold index: {fold_result['fold_index']}")
    print(f"  train: {fold_result['train_period']['start']} .. {fold_result['train_period']['end']}")
    print(f"  validation: {fold_result['validation_period']['start']} .. {fold_result['validation_period']['end']}")
    print(f"  test (locked): {fold_result['test_period']['start']} .. {fold_result['test_period']['end']}")
    print(
        f"  trades: train={fold_result['train_trade_count']} "
        f"val={fold_result['val_trade_count']} test={fold_result['test_trade_count']}"
    )
    print(f"  test PnL: {fold_result['test_total_pnl']:.2f} (commission {fold_result['test_total_commission']:.2f})")
    print(f"  deterministic: {fold_result['test_deterministic']}")
    print(f"  long-only preserved: {fold_result['long_only_preserved']}")
    print("  no strategy or broker evidence generated; platform correctness only")


if __name__ == "__main__":
    main()
