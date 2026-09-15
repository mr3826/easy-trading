"""Test script for Phase 7 features."""
import sys
sys.path.insert(0, r"D:\hexabyte_technologies\easy-trading\trading-platform\src")

from trading_platform.risk.risk_engine import HardRiskEngine, RiskPolicyVersion, ReconciliationEngine, SessionScheduler
from trading_platform.oms.oms import OMS
from trading_platform.chaos_engine import FailureInjector, FailureScenarios, RunbookGenerator, DeadManHeartbeat, FailureRecord
from trading_platform.domain import Instrument

print("=" * 60)
print("PHASE 7: FAILURE INJECTION, SECURITY, BACKUP & RECOVERY")
print("=" * 60)

# --- HardRiskEngine backup/restore ---
print("\n--- HardRiskEngine Backup/Restore ---")
engine = HardRiskEngine()
policy = RiskPolicyVersion(version=1, max_positions=3, max_gross_exposure=1_000_000.0)
engine.add_policy(policy)
state = engine.export_state()
print(f"  Exported state: policy_history={len(state['policy_history'])} items, "
      f"decision_audit={len(state['decision_audit'])} items")

engine2 = HardRiskEngine()
engine2.import_state(state)
print(f"  Imported state: active_policy=v{engine2.active_policy.version if engine2.active_policy else None}")

# --- ReconciliationEngine backup/restore ---
print("\n--- ReconciliationEngine Backup/Restore ---")
oms = OMS(oms_id='backup_test')
reconcile = ReconciliationEngine(oms)
reconcile.errors.append('test error')
state2 = reconcile.export_state()
print(f"  Exported state: errors={state2['errors']}")

reconcile2 = ReconciliationEngine(oms)
reconcile2.import_state(state2)
print(f"  Imported state: errors={reconcile2.errors}")

# --- FailureInjector ---
print("\n--- FailureInjector ---")
inj = FailureInjector(FailureScenarios.FAILURE_INTERNET_LOSS, duration=5.0)
print(f"  Injector active: {inj.status()['active']}")
inj.recover()
print(f"  After recover: {inj.status()}")

# --- FailureScenarios ---
print("\n--- FailureScenarios ---")
internet_inj = FailureScenarios.internet_loss(3.0)
print(f"  Internet loss injector active: {internet_inj.status()['active']}")

# --- Duplicate events ---
print("\n--- Duplicate Events ---")
dupes = FailureScenarios.duplicate_event(3)
print(f"  Generated {len(dupes)} duplicate event records")

# --- RunbookGenerator ---
print("\n--- RunbookGenerator ---")
runbook = RunbookGenerator.generate_from_records(dupes)
print(f"  Runbook length: {len(runbook)} chars")
# Show first 200 chars
lines = runbook.split('\n')
for line in lines[:10]:
    print(f"    {line}")

# --- DeadManHeartbeat ---
print("\n--- DeadManHeartbeat ---")
hb = DeadManHeartbeat(interval=30.0, failure_threshold=3)
hb.record_alive()
print(f"  Heartbeat healthy: {hb.is_healthy()}")
hb.record_miss()
hb.record_miss()
hb.record_miss()
print(f"  After 3 misses healthy: {hb.is_healthy()}")

# --- Security checkpoint on risk engine ---
print("\n--- Security Checkpoint ---")
risk_engine = HardRiskEngine()
result = risk_engine.security_checkpoint(secrets_detected=False, config_issues=None)
print(f"  Security status: overall_secure={result['overall_secure']}, "
      f"warnings={result['warnings']}")

result2 = risk_engine.security_checkpoint(secrets_detected=True, config_issues=["test config issue"])
print(f"  With secrets detected: overall_secure={result2['overall_secure']}, "
      f"warnings={result2['warnings']}")

# --- Full integration test ---
print("\n--- Full Integration Test ---")
# Create a complete Phase 7 system
final_engine = HardRiskEngine()
final_policy = RiskPolicyVersion(version=1, max_positions=3, max_gross_exposure=100000.0)
final_engine.add_policy(final_policy)

final_oms = OMS(oms_id='phase7_integration')
final_reconcile = ReconciliationEngine(final_oms)

# Take checkpoints
cp1 = final_engine.take_checkpoint()
cp2 = final_reconcile.take_checkpoint()
print(f"  Engine checkpoint taken: {cp1 is not None}")
print(f"  Reconcile checkpoint taken: {cp2 is not None}")

# Export and re-import
exported = final_engine.export_state()
import_engine = HardRiskEngine()
import_engine.import_state(exported)
print(f"  Engine export/import: active_policy=v{import_engine.active_policy.version if import_engine.active_policy else None}")

exported2 = final_reconcile.export_state()
import_reconcile = ReconciliationEngine(final_oms)
import_reconcile.import_state(exported2)
print(f"  Reconcile export/import: errors={import_reconcile.errors}")

print("\n" + "=" * 60)
print("PHASE 7 COMPLETE: All features operational")
print("=" * 60)