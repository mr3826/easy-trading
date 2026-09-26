# Operations Runbook

Authoritative runbook: [runbooks/incident-response.md](runbooks/incident-response.md).
Backup/restore: [runbooks/backup-restore.md](runbooks/backup-restore.md).
Shadow operations: [shadow-operator-guide.md](shadow-operator-guide.md).
IBKR paper: [ibkr-paper-guide.md](ibkr-paper-guide.md).

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
