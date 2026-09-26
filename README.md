# Easy Trading Platform

Easy Trading is a reproducible Python research application for daily completed
US-equity bars: it gates data quality point-in-time first, runs registered
strategy families through walk-forward/cost/overfitting validation, applies a
promotion gate, and reports a trustworthy answer — including "no strategy
promoted". Controlled execution machinery exists behind that gate but is not
authorized.

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

The engineering foundation is implemented and CI-verified (packaging, static
checks, tests, PostgreSQL migrations, no-lookahead behavior, data-quality
gates, safety gates). IBKR connectivity, shadow forward operation,
paper-duration validation, and live authorization require separate external
evidence/approval and remain out of scope.

Permanent runtime defaults:

```text
LIVE_TRADING_ENABLED=false
LIVE_STATUS=NOT_AUTHORIZED
```

## MVP-1: the operator workflow

One canonical CLI covers the whole research product:

```bash
uv run trading-platform doctor       # is this machine/checkout operable?
uv run trading-platform status       # where does the project stand?
uv run trading-platform data build-membership --csv vendor.csv --output manifest.json
uv run trading-platform data preflight --data-dir <bars> --benchmark SPY --membership manifest.json
uv run trading-platform research run-all --data-dir <bars> --benchmark SPY --membership manifest.json
uv run trading-platform report show  # MVP_RESEARCH_SUMMARY.md
```

A successful MVP-1 run ends with a promotion-gate verdict — and
**NO STRATEGY PROMOTED is a valid answer**. Real research evidence requires
licensed point-in-time data: see [docs/MVP1.md](docs/MVP1.md) and
[docs/DATA_SOURCING.md](docs/DATA_SOURCING.md).

## Quick Start

```text
uv sync --all-extras --locked
uv run trading-platform doctor
uv run pytest -m "not external"
```

PostgreSQL integration tests require an isolated `DATABASE_URL`. See
`docs/local-development.md`.

## Documentation

- MVP-1 product guide (start here): `docs/MVP1.md`
- MVP-1 readiness matrix: `MVP1_READINESS.md`
- Architecture: `docs/architecture-overview.md`
- Data procurement: `docs/DATA_SOURCING.md`
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
