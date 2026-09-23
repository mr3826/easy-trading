# Final Verification Evidence

## Source

- Tested source SHA: `7f7cab653d1c791aba426a68327e3bbdb577d741`
- Branch: `integration/remaining-platform-work`
- Tested at: `2026-09-22T23:10:03Z` (targeted proofs at `2026-09-22T23:11:23Z`)
- Environment: Windows, CPython 3.14.3, uv locked environment on `E:\kilo-venvs\integration`
- Database: isolated local PostgreSQL 16 container `agent-c-pg16-test`, `127.0.0.1:5433`

## Commands and Results

| Command | Exit | Result |
|---|---:|---|
| `uv sync --all-extras --locked` | 0 | 61 packages resolved/audited |
| `uv run python -c "import trading_platform"` | 0 | Production import succeeded |
| `uv run python scripts/import_smoke.py` | 0 | All production modules imported |
| `uv run ruff check .` | 0 | Clean |
| `uv run ruff format --check .` | 0 | 98 files formatted |
| `uv run mypy trading-platform/src` | 0 | 35 source files, no issues |
| `uv run pip-audit` | 0 | No known vulnerabilities; local project not on PyPI was skipped |
| `uv run python scripts/secret_scan.py` | 0 | No high-confidence secret candidates |
| `uv run pytest -m postgres -q` | 0 | 2 passed |
| `uv run pytest -m "not external" --cov=trading_platform --cov-report=term-missing --cov-report=xml` | 0 | 228 passed, 2 external tests deselected |
| Critical coverage report with `--fail-under=90` | 0 | 93% aggregate (`oms 95%`, `risk_engine 91%`, `reconciliation 100%`, `authorization 93%`) |
| `uv run pytest -m external --collect-only -q` | 0 | 2 collected, not executed |

Overall production coverage: **89%**.

## Targeted Proofs

Command:

```text
uv run pytest -q \
  trading-platform/tests/unit/test_research_robustness.py::test_generate_signal_as_of_excludes_future_bars \
  trading-platform/tests/unit/test_oms_reconciliation.py::test_internal_snapshot_matches_independent_broker_snapshot \
  trading-platform/tests/unit/test_oms_reconciliation.py::test_fail_closed_chain_wires_incident_alert_and_resolution \
  trading-platform/tests/unit/test_safety_boundaries.py::test_live_authorization_is_permanently_disabled \
  trading-platform/tests/unit/test_safety_boundaries.py::test_default_config_cannot_authorize_live \
  trading-platform/tests/integration/test_postgres_store.py::test_postgres_migration_idempotency_and_recovery
```

Exit code: 0; **6 passed** at `2026-09-22T23:11:23Z`.

Covered proofs: no-lookahead, independently sourced reconciliation, mismatch
fail-closed behavior, no-live authority, PostgreSQL migration idempotency, and
restart/recovery checks.

## External and Manual Gates

- Real IBKR paper connection: **not performed**.
- Paper order submission: **not performed**.
- 60-day paper/shadow observation: **not performed**.
- Real vendor data validation/licensing: **not performed**; deterministic fake and local Parquet fixtures used.
- Real alert delivery (SMTP/webhook/Telegram): **not performed**; fake transports tested.
- External dead-man deployment: **not performed**.
- Production clean-room backup restore drill: **not performed**; local encrypted round-trip tested.
- Strategy profitability or forward performance: **not claimed**.
- Live authorization: permanently disabled; `LIVE_TRADING_ENABLED=false`, `LIVE_STATUS=NOT_AUTHORIZED`.

## Evidence Ceiling

These results prove code and local CI behavior at the tested SHA only. They do
not prove external connectivity, deployment, account authorization, vendor
licensing, forward performance, or live-trading readiness.
