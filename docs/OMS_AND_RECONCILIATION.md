# OMS and Reconciliation

OMS: `oms/oms.py`. Reconciliation: `reconciliation/` + `risk.ReconciliationEngine` —
**IMPLEMENTED, TESTED, CI-VERIFIED (>=90% critical coverage)**.

## OMS

- Full order lifecycle (submit/accept/open/fill/cancel/replace) with a durable event ledger
  and deterministic rebuild (`rebuild_from_ledger`) for restart recovery.
- Idempotency-key dedupe; OCA groups for brackets; protective-order coverage invariant
  (`check_protective_invariant`).
- Timeouts, partial fills, and late fills handled and incident-recorded.

## Reconciliation

- Positions, orders, fills, cash reconciled against broker snapshots
  (`reconcile_all`, `reconcile_against_snapshot`); paper-session validation
  (`validate_paper_session`) supports IBKR paper checks.
- Any mismatch triggers the fail-closed incident chain (`FailClosedChain`): persist +
  alert + block new orders until an operator resolves (`resolve` requires an operator id).
- The paper orchestrator refuses to submit while `reconciliation_healthy()` is false.
