# Disaster Recovery

Authoritative procedure: [runbooks/backup-restore.md](runbooks/backup-restore.md).

- Encrypted, checksum-verified backups (`backup.py`).
- OMS rebuilds deterministically from its event ledger (`OMS.rebuild_from_ledger`).
- The decision journal is append-only with fsync; post-restart replays are no-ops
  (tested via orchestrator idempotency tests).
- After any restore: run full reconciliation before resuming; unresolved incidents block
  new exposure by default.
