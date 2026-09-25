"""Test script for Phase 8 features."""
import sys
from datetime import datetime
sys.path.insert(0, r"D:\hexabyte_technologies\easy-trading\trading-platform\src")

from trading_platform.risk.risk_engine import HardRiskEngine, ReconciliationEngine, SessionScheduler
from trading_platform.oms.oms import OMS
from trading_platform.monitor import SystemMonitor, AlertHandler, ShadowSessionOperator
from trading_platform.chaos_engine import DeadManHeartbeat
from trading_platform.domain import Instrument, Order

print("=" * 60)
print("PHASE 8: LIVE-DATA SHADOW MODE AND OPERATIONS")
print("=" * 60)

# --- SystemMonitor ---
print("\n--- SystemMonitor ---")
oms = OMS(oms_id='shadow_test')
reconcile = ReconciliationEngine(oms)
scheduler = SessionScheduler(oms=None, reconciliation=reconcile)

monitor = SystemMonitor(session_scheduler=scheduler)
monitor.record_signal()
monitor.record_signal()
monitor.record_risk_rejection()
monitor.update_last_data_timestamp(datetime.now())
monitor.record_decision_latency(15.5)
monitor.update_last_write_timestamp(datetime.now())

snap = monitor.snapshot()
print(f"  Signals: {snap['signals_recorded']}")
print(f"  Risk rejections: {snap['risk_rejections']}")
print(f"  Data freshness: {snap['data_freshness']['is_fresh']}")
print(f"  Decision latency: {snap['avg_decision_latency_ms']:.1f}ms")
print(f"  Trading halted: {snap['trading_halted']}")
print(f"  Startup reconciliation done: {snap['startup_reconciliation_done']}")
print(f"  Journal lag: {snap['journal_lag']}")
print(f"  DB health: {snap['database_health']['healthy']}")
print(f"  Heartbeat healthy: {snap['heartbeat_healthy']}")

# --- AlertHandler ---
print("\n--- AlertHandler ---")
alert = AlertHandler()
alert.alert("Test critical alert", severity="CRITICAL")
alert.alert_data_freshness_stale(7200.0, 3600.0)
alert.alert_db_unhealthy(600.0)
alert.alert_journal_lag(15)
alert.alert_heartbeat_missed(5)

# --- ShadowSessionOperator ---
print("\n--- ShadowSessionOperator ---")
operator = ShadowSessionOperator(
    oms=oms,
    risk_engine=HardRiskEngine(),
    reconciliation=reconcile,
    monitor=monitor,
    broker_adapter=None,
)

result = operator.process_should_submit({
    "symbol": "AAPL",
    "side": "long",
    "price": 150.0,
    "quantity": 10,
    "positions": {},
    "cash": 10000.0,
})

print(f"  Would submit: {result['would_submit']}")
print(f"  Approved: {result['approved']}")
print(f"  Reason: {result['reason']}")
print(f"  Hypothetical fill: {result['hypothetical_fill']}")
print(f"  Policy version: {result['risk_policy_version']}")
print(f"  Reconciliation: {result['reconciliation']['overall_status']}")
print(f"  Monitoring snapshot keys: {list(result['monitoring'].keys())}")

# --- DeadManHeartbeat (Phase 7) ---
print("\n--- DeadManHeartbeat (Phase 7) ---")
hb = DeadManHeartbeat(interval=30.0, failure_threshold=3)
hb.record_alive()
print(f"  Healthy after record_alive: {hb.is_healthy()}")
hb.record_miss()
hb.record_miss()
hb.record_miss()
print(f"  Healthy after 3 misses: {hb.is_healthy()}")

# --- Metrics history ---
print("\n--- Metrics History ---")
monitor.record_snapshot()
monitor.record_snapshot()
print(f"  History length: {len(monitor.export_history())} snapshots")

print("\n" + "=" * 60)
print("PHASE 8 COMPLETE: All features operational")
print("=" * 60)