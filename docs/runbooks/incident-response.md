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
overwrite the mismatch. A shadow replay mismatch is persisted to
`discrepancies.jsonl` and raised as `ShadowReplayMismatch`; treat it the same
way.

## Stale or Revised Data

Reject the affected decision, preserve the raw input and provenance, mark the
data discrepancy, and wait for a fresh completed bar or authorized correction.
The shadow orchestrator persists the problem to `problems.jsonl` and skips the
affected bar automatically.

## Process or Disk Failure

Keep submissions disabled, restore from a verified encrypted backup if needed,
run migrations, recover open orders, and reconcile before restart completion.

## Security or Secret Exposure

Disable affected credentials, block all submissions, preserve sanitized audit
evidence, rotate through the approved secret manager, and investigate logs and
artifacts without copying the secret.

## Dead-Man Deployment (External)

The dead-man monitor is an independent process outside the trading VM's
failure domain. Deployment steps (configuration templates, no real values):

1. Install the platform package on a separate monitoring host
   (`uv sync --all-extras --locked`).
2. Run the dead-man CLI as its own process with a system service manager
   (for example a systemd unit or Windows service) so it restarts
   independently of the trading process:

   ```
   dead-man-monitor <heartbeat-path> --max-age-seconds <seconds>
   ```

   Exit code 0 means the heartbeat file is fresh; any other exit code means
   the trading process is presumed dead and must be treated as an incident.
3. Point the trading process's `DeadManHeartbeat(alert_transport=...)` at a
   configured alert channel so unhealthy heartbeats page the operator.
4. Write the trading process's heartbeat file on every loop iteration; the
   monitoring host only reads it.
5. Verify the monitoring host can alert when the trading VM is unreachable,
   and record the drill evidence outside source control.
