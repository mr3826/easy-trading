# Incident Runbooks

## Database Loss

Block new orders, persist or queue an incident if possible, alert both
channels, and do not resume until startup recovery and reconciliation succeed.

## Broker Disconnect or Rejection

Stop new submissions, preserve local and broker identifiers, query open and
completed orders after reconnect, reconcile fills and positions, and require
verified resolution before resuming.

## Reconciliation Mismatch

Set `BLOCK_NEW_ORDERS`, persist a critical incident, send independent alerts,
and compare independently derived local and broker state. Do not silence or
overwrite the mismatch.

## Stale or Revised Data

Reject the affected decision, preserve the raw input and provenance, mark the
data discrepancy, and wait for a fresh completed bar or authorized correction.

## Process or Disk Failure

Keep submissions disabled, restore from a verified encrypted backup if needed,
run migrations, recover open orders, and reconcile before restart completion.

## Security or Secret Exposure

Disable affected credentials, block all submissions, preserve sanitized audit
evidence, rotate through the approved secret manager, and investigate logs and
artifacts without copying the secret.
