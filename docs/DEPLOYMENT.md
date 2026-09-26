# Deployment / Operations / Testing / Recovery (pointers)

- **Local development & deployment**: `local-development.md`, `environment.md`;
  CI pipeline `.github/workflows/ci.yml` (uv locked sync, import smoke, ruff, format, mypy,
  pytest + coverage >=80% overall / >=90% critical paths, pip-audit, gitleaks).
- **Operations runbook**: `runbooks/incident-response.md`; alert setup: `alert-setup.md`.
- **Disaster recovery**: `runbooks/backup-restore.md` (encrypted, checksummed backups;
  ledger-based OMS rebuild).
- **Testing strategy**: `test-strategy.md` — unit, property-based (hypothesis), integration,
  PostgreSQL service tests, chaos/recovery, no-lookahead and safety-boundary gates;
  external IBKR tests are explicitly gated markers.

This file intentionally consolidates four checklist items (`DEPLOYMENT.md`,
`OPERATIONS_RUNBOOK.md`, `TESTING.md`, `DISASTER_RECOVERY.md`) to avoid duplicating
documents that already exist and are maintained.
