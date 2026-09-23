# Backup and Restore Runbook

## Backup

1. Stop new submissions and record the current commit and migration version.
2. Export the PostgreSQL journal and execution state using an approved database
   backup tool.
3. Encrypt the backup with a deployment-managed AES-GCM key.
4. Store the ciphertext and its SHA-256 checksum in separate controlled
   locations.
5. Record retention, owner, and expiration metadata without storing secrets.

### Archive-Level Backup

`backup.encrypt_directory(source_dir, destination, key)` encrypts a directory
of files (for example a shadow archive plus a journal export) as a single
deterministic tar with per-file SHA-256 checksums in an embedded manifest, and
returns the ciphertext checksum for verification before restore. Files are
added in sorted name order with fixed metadata, so the payload is
deterministic for fixed file contents.

```
uv run python -c "from pathlib import Path; from trading_platform.backup import encrypt_directory
key = <key-from-secret-store>
print(encrypt_directory(Path('<archive-dir>'), Path('<destination>.enc'), key))"
```

Record the returned ciphertext checksum next to the ciphertext in a separate
controlled location; do not commit it to source control.

## Restore

1. Declare an incident and keep new submissions blocked.
2. Provision a clean isolated PostgreSQL instance and a clean filesystem
   directory owned by the restore operator.
3. Verify the ciphertext checksum before decryption
   (`restore_backup(..., expected_checksum=<recorded>)` refuses on mismatch).
4. Decrypt only into the clean restore environment:
   `backup.restore_backup(archive, dest, key)` rebuilds every file, verifies
   per-file checksums and the embedded manifest, and raises on any mismatch,
   missing manifest, or unsafe member name before a file is written.
5. Apply migrations and verify schema version.
6. Restore the append-only journal and OMS state.
7. Rebuild the same economic state from the restored artifacts: replay the
   restored shadow archive with `ShadowOrchestrator.verify_replay()` and
   recompute journal lag from the restored journal export; both must match
   the pre-incident state deterministically.
8. Reconcile positions, orders, fills, cash, and protective coverage against
   an independently derived source.
9. Resolve all discrepancies before considering any paper or live action.

The automated tests prove encryption, checksum verification, deterministic
archive round trips, migrations, and open-order recovery. They do not
constitute a real operational restore drill.

## Restore Drill (External Gate)

A real restore drill remains an external gate and is not performed by this
repository's automation:

1. Schedule a drill window with the operator on call and declare the drill in
   the incident channel.
2. Take a real encrypted archive-level backup of a shadow archive and journal
   export using the deployment secret store.
3. Restore into an isolated clean-room host (not the trading VM) with a fresh
   key copy from the secret manager.
4. Verify the rebuilt economic state per step 7 and record the drill evidence
   (timestamps, checksums, outcomes) outside source control.
5. Confirm the drill window and evidence with the operator before closing it.
