# Operations Runbook

Authoritative runbook: [runbooks/incident-response.md](runbooks/incident-response.md).
Backup/restore: [runbooks/backup-restore.md](runbooks/backup-restore.md).
Shadow operations: [shadow-operator-guide.md](shadow-operator-guide.md).
IBKR paper: [ibkr-paper-guide.md](ibkr-paper-guide.md).
MVP-1 research workflow: [MVP1.md](MVP1.md).

## MVP-1 daily research checklist (no external services required)

1. `uv run trading-platform doctor` — expect `MVP_STATUS=...` and a non-zero
   exit while research data is not yet configured (that is correct, not a
   failure to fix in code).
2. On vendor delivery: `data build-membership` then `data preflight`.
   FAIL (exit 2) => reject the dataset, contact vendor. Exit 1 => list every
   warning for the operator; only then use `--accept-data-warnings`.
3. `research run-all --data-dir ... --benchmark SPY --membership ...` —
   NO STRATEGY PROMOTED (exit 0) is a valid completed answer.
4. Read `artifacts/research/<run-id>/MVP_RESEARCH_SUMMARY.md`; the run,
   fingerprint, commit and policy hash are pinned there.

## Daily paper-operation checklist

1. Heartbeat fresh (`dead_man` / `DeadManHeartbeat`).
2. Reconciliation status CLEAN (no unresolved incidents).
3. No latched kill-switch conditions; health snapshot all-green
   ([KILL_SWITCHES.md](KILL_SWITCHES.md)).
4. Promotion artifact present and policy-current for the pinned strategy version.
5. Drift monitor state HEALTHY/WATCH for the active strategy
   ([STRATEGY_DRIFT.md](STRATEGY_DRIFT.md)).
6. Journal writable; snapshots exporting.

If any step fails: do not operate. Blocks are fail-closed by design.
