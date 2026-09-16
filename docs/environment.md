# Environment Guide

## Required Safety Variables

| Variable | Required value | Meaning |
|---|---|---|
| `LIVE_TRADING_ENABLED` | `false` | Permanent repository default; true is rejected by config |
| `LIVE_STATUS` | `NOT_AUTHORIZED` | Required final runtime state |
| `TRADING_ENV` | `research`, `simulation`, `shadow`, or `paper` for ordinary work | Selects a non-live environment |
| `DATABASE_URL` | isolated test PostgreSQL URL for integration tests | Durable state service |

Credentials are deployment secrets, not source-controlled configuration.

## Environment Separation

Research and simulation use local deterministic inputs. Shadow mode can archive
real completed-bar inputs but records only `WOULD_SUBMIT`. Paper mode requires a
separately configured paper account and explicit external test authorization.
Live mode additionally requires all authorization gates and still remains
disabled by repository policy.

Paper credentials must not be accepted as live credentials. Recognized live
ports/accounts must be rejected by the IBKR boundary. A single environment
variable cannot authorize live trading.

## Secret Handling

Use an external secret manager or CI secret store. The backup module accepts a
key from its caller, encrypts with AES-GCM, and never persists the key. Alert
passwords are supplied by a callback and are not logged.
