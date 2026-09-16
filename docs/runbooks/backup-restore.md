# Backup and Restore Runbook

## Backup

1. Stop new submissions and record the current commit and migration version.
2. Export the PostgreSQL journal and execution state using an approved database
   backup tool.
3. Encrypt the backup with a deployment-managed AES-GCM key.
4. Store the ciphertext and its SHA-256 checksum in separate controlled
   locations.
5. Record retention, owner, and expiration metadata without storing secrets.

## Restore

1. Declare an incident and keep new submissions blocked.
2. Provision a clean isolated PostgreSQL instance.
3. Verify the ciphertext checksum before decryption.
4. Decrypt only into the clean restore environment.
5. Apply migrations and verify schema version.
6. Restore the append-only journal and OMS state.
7. Reconcile positions, orders, fills, cash, and protective coverage against
   an independently derived source.
8. Resolve all discrepancies before considering any paper or live action.

The automated tests prove encryption, checksum verification, migrations, and
open-order recovery. They do not constitute a real operational restore drill.
