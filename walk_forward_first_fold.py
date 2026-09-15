"""Run first walk-forward fold with MA crossover hypothesis."""

import sys
import datetime
import hashlib
from pathlib import Path

# Add src to path
sys.path.insert(0, r"D:\hexabyte_technologies\easy-trading\trading-platform\src")

# Manually define compute_dataset_hash since import is problematic
def compute_dataset_hash(bars_data: str) -> str:
    """Compute SHA-256 hash of dataset for reproducibility."""
    return hashlib.sha256(bars_data.encode()).hexdigest()


# Import other modules directly via exec
import importlib.util

# Load walk_forward module
spec = importlib.util.spec_from_file_location(
    "walk_forward_module",
    r"D:\hexabyte_technologies\easy-trading\trading-platform\src\trading_platform\walk_forward\walk_forward.py"
)
wf_mod = importlib.util.module_from_spec(spec)
# Remove problematic import by patching before exec
orig_import = __builtins__.__dict__.get('__import__', __import__)
# We'll just import what we need directly

# Load persistence module
spec2 = importlib.util.spec_from_file_location(
    "persistence_experiment",
    r"D:\hexabyte_technologies\easy-trading\trading-platform\src\trading_platform\persistence\experiment.py"
)
pexp = importlib.util.module_from_spec(spec2)

# Load risk module
spec3 = importlib.util.spec_from_file_location(
    "risk_limit",
    r"D:\hexabyte_technologies\easy-trading\trading-platform\src\trading_platform\risk\limit.py"
)
prisk = importlib.util.module_from_spec(spec3)

# Now exec them
spec.loader.exec_module(wf_mod)
spec2.loader.exec_module(pexp)
spec3.loader.exec_module(prisk)

# Now get the classes we need
PeriodSplit = wf_mod.PeriodSplit
WalkForwardEvaluator = wf_mod.WalkForwardEvaluator
ExperimentRegistry = pexp.ExperimentRegistry

# Load strategies module
spec4 = importlib.util.spec_from_file_location(
    "ma_cross_strategy",
    r"D:\hexabyte_technologies\easy-trading\trading-platform\src\trading_platform\strategies\ma_cross_strategy.py"
)
mcs = importlib.util.module_from_spec(spec4)
spec4.loader.exec_module(mcs)
MaCrossHypothesis = mcs.MaCrossHypothesis
generate_signal = mcs.generate_signal

# Load simulator
spec5 = importlib.util.spec_from_file_location(
    "event_driven_simulator",
    r"D:\hexabyte_technologies\easy-trading\trading-platform\src\trading_platform\simulator\event_driven_simulator.py"
)
sim_mod = importlib.util.module_from_spec(spec5)
spec5.loader.exec_module(sim_mod)
EventDrivenSimulator = sim_mod.EventDrivenSimulator

print("=" * 60)
print("PHASE 5: WALK-FORWARD VALIDATION - FIRST FOLD")
print("=" * 60)

# 1. Setup period split
split = PeriodSplit(train_days=60, validation_days=20, test_days=30)

# 2. Define data range (using 1 year of mock data)
all_dates = [datetime.date(2020, 1, 1) + datetime.timedelta(days=i) for i in range(365)]

# 3. Split into train/validation/test
periods = split.split(all_dates[0], all_dates[-1])
train_dates = [d for d in all_dates if periods["train"][0] <= d <= periods["train"][1]]
val_dates = [d for d in all_dates if periods["validation"][0] <= d <= periods["validation"][1]]
test_dates = [d for d in all_dates if periods["test"][0] <= d <= periods["test"][1]]

print(f"\nPeriod split:")
print(f"  Train: {len(train_dates)} days ({periods['train'][0]} to {periods['train'][1]})")
print(f"  Val:   {len(val_dates)} days ({periods['validation'][0]} to {periods['validation'][1]})")
print(f"  Test:  {len(test_dates)} days ({periods['test'][0]} to {periods['test'][1]})")

# 4. Generate mock bar data for each period
bars_by_symbol = {
    "AAPL": {
        **{d.isoformat(): [type("Bar", (), {
            "instrument": type("Inst", (), {"symbol": "AAPL"})(),
            "timestamp": datetime.datetime(d.year, d.month, d.day, 16, 0, 0),
            "open": 100.0 + (d.day % 20 - 10) * 0.5 + (d.month % 12 - 6) * 0.3,
            "high": 102.0 + (d.day % 20 - 10) * 0.5 + (d.month % 12 - 6) * 0.3,
            "low": 98.0 + (d.day % 20 - 10) * 0.5 + (d.month % 12 - 6) * 0.3,
            "close": 100.0 + (d.day % 20 - 10) * 0.5 + (d.month % 12 - 6) * 0.3,
            "volume": 1000,
        })() for d in train_dates if d.isoformat() in [x.isoformat() for x in train_dates]],
        **{d.isoformat(): [type("Bar", (), {
            "instrument": type("Inst", (), {"symbol": "AAPL"})(),
            "timestamp": datetime.datetime(d.year, d.month, d.day, 16, 0, 0),
            "open": 100.0 + (d.day % 20 - 10) * 0.5 + (d.month % 12 - 6) * 0.3,
            "high": 102.0 + (d.day % 20 - 10) * 0.5 + (d.month % 12 - 6) * 0.3,
            "low": 98.0 + (d.day % 20 - 10) * 0.5 + (d.month % 12 - 6) * 0.3,
            "close": 100.0 + (d.day % 20 - 10) * 0.5 + (d.month % 12 - 6) * 0.3,
            "volume": 1000,
        })() for d in val_dates if d.isoformat() in [x.isoformat() for x in val_dates]],
        **{d.isoformat(): [type("Bar", (), {
            "instrument": type("Inst", (), {"symbol": "AAPL"})(),
            "timestamp": datetime.datetime(d.year, d.month, d.day, 16, 0, 0),
            "open": 100.0 + (d.day % 20 - 10) * 0.5 + (d.month % 12 - 6) * 0.3,
            "high": 102.0 + (d.day % 20 - 10) * 0.5 + (d.month % 12 - 6) * 0.3,
            "low": 98.0 + (d.day % 20 - 10) * 0.5 + (d.month % 12 - 6) * 0.3,
            "close": 100.0 + (d.day % 20 - 10) * 0.5 + (d.month % 12 - 6) * 0.3,
            "volume": 1000,
        })() for d in test_dates if d.isoformat() in [x.isoformat() for x in test_dates]],
    }
}

# 5. Setup hypothesis (PROVISIONAL parameters, NOT to be optimized)
hypothesis = MaCrossHypothesis(
    fast_length=5,
    slow_length=20,
    max_positions=3,
    commission_per_order=1.0,
    slippage_pct=0.001,
)

print(f"\nHypothesis: {hypothesis.hypothesis_id}")
print(f"  Fast SMA: {hypothesis.fast_length}, Slow SMA: {hypothesis.slow_length}")
print(f"  Commission: ${hypothesis.commission_per_order}/order")
print(f"  Slippage: {hypothesis.slippage_pct:.0%}")

# 5. Setup simulator
simulator = EventDrivenSimulator(
    mode="DETERMINISTIC",
    start_cash=10000.0,
    fill_assumption="CLOSE",
)

# 6. Setup registry
registry = ExperimentRegistry()

# 7. Run walk-forward evaluator on FIRST fold only (to verify infrastructure)
evaluator = WalkForwardEvaluator(
    simulator=simulator,
    hypothesis=hypothesis,
    period_split=split,
    registry=registry,
)

# Run first fold only (fold_index=0)
fold_result = evaluator.run_fold(
    fold_index=0,
    fold={
        "train": (periods["train"][0], periods["train"][1]),
        "validation": (periods["validation"][0], periods["validation"][1]),
        "test": (periods["test"][0], periods["test"][1]),
    },
    bars_by_symbol=bars_by_symbol,
    symbols=["AAPL"],
)

# 8. Aggregate results
agg = evaluator.aggregate_results()

# 9. Bootstrap drawdown distribution
bootstrap = evaluator.bootstrap_drawdown_distribution(n_resamples=1000, seed=42)

# 10. Report
print(f"\n{'=' * 60}")
print("FOLD 0 RESULTS (FIRST WALK-FORWARD FOLD)")
print(f"{'=' * 60}")

print(f"\n  TRAIN period metrics:")
print(f"    Final cash:      ${fold_result['train_final_cash']:.2f}")
print(f"    Total PnL:       ${fold_result['train_total_pnl']:.2f}")
print(f"    Trade count:     {fold_result['train_trade_count']}")

print(f"\n  VALIDATION period metrics:")
print(f"    Final cash:      ${fold_result['val_final_cash']:.2f}")
print(f"    Total PnL:       ${fold_result['val_total_pnl']:.2f}")
print(f"    Trade count:     {fold_result['val_trade_count']}")

print(f"\n  TEST period metrics (LOCKED - no tuning):")
print(f"    Final cash:      ${fold_result['test_final_cash']:.2f}")
print(f"    Total PnL:       ${fold_result['test_total_pnl']:.2f}")
print(f"    Trade count:     {fold_result['test_trade_count']}")
print(f"    Commission:      ${fold_result['test_total_commission']:.2f}")
print(f"    Slippage:        ${fold_result['test_total_slippage']:.2f}")

print(f"\n  AGGREGATED ACROSS FOLDS (only 1 fold run):")
print(f"    OOS expectancy:      ${agg['oos_expectancy_mean']:.2f}")
print(f"    OOS expectancy spread: ${agg['oos_expectancy_spread']:.2f}")
print(f"    Bonferroni alpha:    {agg['bonferroni_alpha']:.4f}")
print(f"    Cash spread (test):  ${agg['cash_spread_test']:.2f}")
print(f"    PnL spread (test):   ${agg['pnl_spread_test']:.2f}")

print(f"\n  BOOTSTRAP DRAWDOWN DISTRIBUTION (1000 resamples):")
print(f"    Mean drawdown:     {bootstrap['mean']:.4f}")
print(f"    5th percentile:    {bootstrap['p5']:.4f}")
print(f"    95th percentile:   {bootstrap['p95']:.4f}")
print(f"    10th percentile:   {bootstrap['p10']:.4f}")
print(f"    90th percentile:   {bootstrap['p90']:.4f}")
print(f"    Min drawdown:      {bootstrap['min']:.4f}")
print(f"    Max drawdown:      {bootstrap['max']:.4f}")

# 10. G4/S4 criteria check
print(f"\n  G4/S4 CRITERIA CHECK:")
oos_mean = agg["oos_expectancy_mean"]
oos_spread = agg["oos_expectancy_spread"]
print(f"    Out-of-sample expectancy: ${oos_mean:.2f} (with spread ±{oos_spread:.2f})")
if oos_mean > 0:
    print(f"    ✅ PASS: Positive expectancy after costs")
else:
    print(f"    ❌ FAIL: Negative expectancy after costs - hypothesis needs revision")
if oos_spread < abs(oos_mean) * 0.5:
    print(f"    ✅ PASS: Spread is <= 50% of mean (robust)")
else:
    print(f"    ❌ FAIL: Spread too large relative to mean (unstable)")

print(f"\n  {'=' * 60}")
print("NEXT STEPS:")
print(f"  1. Run remaining walk-forward folds (increase fold count)")
print(f"  2. Perform sensitivity analysis on SMA lengths")
print(f"  3. Increase commission/slippage, rerun for robustness")
print(f"  4. If G4/S4 criteria met, proceed to Phase 6 (OMS/risk)")
print(f"  5. If not, revise hypothesis parameters and rerun")
print(f"{'=' * 60}")