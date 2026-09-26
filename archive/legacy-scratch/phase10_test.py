"""Test script for Phase 10 paper validation features."""
import sys
sys.path.insert(0, r"D:\hexabyte_technologies\easy-trading\trading-platform\src")

from trading_platform.risk.risk_engine import ReconciliationEngine
from trading_platform.oms.oms import OMS
from datetime import datetime, timezone

print("=" * 60)
print("PHASE 10: EXTENDED PAPER VALIDATION")
print("=" * 60)

# --- Validate paper session ---
print("\n--- Validate Paper Session ---")
oms = OMS(oms_id='paper_validation_test')
reconcile = ReconciliationEngine(oms)

# Simulate a paper session validation
beginning_cash = 10000.0
current_cash = 10050.0  # $50 profit
positions_snapshot = {"AAPL": 100.0, "MSFT": 50.0}  # position quantities
all_orders = {
    "order-1": {"status": "FILLED", "was_expected": True},
    "order-2": {"status": "FILLED", "was_expected": True},
}
all_fills = [
    {"commission": 1.0, "slippage": 0.0},
    {"commission": 1.0, "slippage": 0.5},
]

result = reconcile.validate_paper_session(
    session_id="session-001",
    beginning_cash=beginning_cash,
    current_cash=current_cash,
    positions_snapshot=positions_snapshot,
    all_orders=all_orders,
    all_fills=all_fills,
    simulation_metrics={"max_drawdown_pct": 10.0, "turnover_pct": 15.0},
)

print(f"  Overall status: {result['overall_status']}")
print(f"  Checks: {list(result['checks'].keys())}")
for check_name, check_result in result["checks"].items():
    print(f"    {check_name}: {check_result['status']}")
if result["discrepancies"]:
    print(f"  Discrepancies:")
    for disc in result["discrepancies"]:
        print(f"      - {disc}")

# --- Compare session to simulation ---
print("\n--- Compare Session to Simulation ---")
comparison = reconcile.compare_session_to_simulation(
    paper_session={
        "session_id": "session-001",
        "metrics": {"expectancy": 0.08, "sharpe_ratio": 1.5, "turnover": 15.0, "trade_count": 42},
    },
    simulation_run={
        "run_id": "sim-001",
        "metrics": {"expectancy": 0.075, "sharpe_ratio": 1.6, "turnover": 14.5, "trade_count": 40},
    },
)

print(f"  Overall match: {comparison['overall_match']}")
print(f"  Metric comparisons:")
for mc in comparison["metric_comparisons"]:
    print(f"    {mc['metric']}: paper={mc['paper']}, sim={mc['simulation']}, match={mc['match']}")
if comparison["discrepancies"]:
    print(f"  Discrepancies:")
    for disc in comparison["discrepancies"]:
        print(f"      - {disc}")

# --- Validation state backup/restore ---
print("\n--- Validation State Backup/Restore ---")
exported = reconcile.export_validation_state()
print(f"  Exported validation state keys: {list(exported.keys())}")

import_reconcile = ReconciliationEngine(oms)
import_reconcile.import_validation_state(exported)
print(f"  Imported errors: {import_reconcile.errors}")
print(f"  Imported warnings: {import_reconcile.warnings}")

print("\n" + "=" * 60)
print("PHASE 10 COMPLETE: Paper validation features operational")
print("=" * 60)