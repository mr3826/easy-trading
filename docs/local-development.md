# Local Development

## Install

Use a clean checkout and the locked dependency set:

```text
uv sync --all-extras --locked
uv run python -c "import trading_platform"
```

## Unit Tests

The configured test root is `trading-platform/tests`. Run non-external tests:

```text
uv run pytest -m "not external" -q
```

Collection must use explicit paths; do not rely on recursive fallback.

## PostgreSQL Tests

Provision an isolated PostgreSQL instance. Never point tests at a shared or
production database. In PowerShell:

```powershell
$env:DATABASE_URL = "postgresql://postgres:postgres@localhost:55434/trading_platform_test"
uv run pytest -m postgres -q
```

The integration test applies migrations, verifies idempotency and append-only
behavior, exercises repository writes, recovers open orders, and verifies
database-disconnected fail-closed behavior.

## Quality Gates

```text
uv run ruff check .
uv run ruff format --check .
uv run mypy trading-platform/src
uv run pytest -m "not external" --cov=trading_platform --cov-report=term-missing
uv run pip-audit
uv run python scripts/secret_scan.py
```

The CI workflow adds PostgreSQL, Gitleaks, overall coverage, critical-module
coverage, and machine-readable artifacts.

## Safe Defaults

Do not add credentials to `.env`, fixtures, logs, or artifacts. Do not run
paper or live submission experiments from ordinary unit tests. External tests
must be explicitly selected and independently authorized.
