# Easy Trading Platform

Easy Trading is a modular Python foundation for research and controlled
execution of daily completed US-equity bars.

## V1 Scope

- Long-only US equities
- Daily completed bars
- Cash account and no leverage
- Maximum three positions
- No extended-hours trading
- No scheduled earnings holds initially

Research, simulation, shadow, paper, and live environments are separate. The
live path is disabled by policy and is not authorized by this repository.

## Safety Status

The coding-verifiable foundation is in progress. CI proves packaging, static
checks, tests, PostgreSQL migrations, no-lookahead behavior, and safety gates.
IBKR connectivity, shadow forward operation, paper-duration validation, and
live authorization require separate approval and evidence.

Permanent runtime defaults:

```text
LIVE_TRADING_ENABLED=false
LIVE_STATUS=NOT_AUTHORIZED
```

## Quick Start

```text
uv sync --all-extras --locked
uv run trading-platform --status
uv run pytest -m "not external"
```

PostgreSQL integration tests require an isolated `DATABASE_URL`. See
`docs/local-development.md`.

## Documentation

- Architecture: `docs/architecture-overview.md`
- Local development: `docs/local-development.md`
- Environment and safety variables: `docs/environment.md`
- Test strategy: `docs/test-strategy.md`
- Shadow operations: `docs/shadow-operator-guide.md`
- IBKR paper boundary: `docs/ibkr-paper-guide.md`
- Alert setup: `docs/alert-setup.md`
- Backup and restore: `docs/runbooks/backup-restore.md`
- Incident response: `docs/runbooks/incident-response.md`
- Threat model: `docs/threat-model.md`
- Audit and phase evidence: `docs/audit/`

No command in this repository submits a live order.
