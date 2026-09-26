# Testing

Strategy: [test-strategy.md](test-strategy.md). CI: `.github/workflows/ci.yml`.

## Layers

- **Unit**: every module; feature correctness, warmup, sizing, statistics, promotion logic.
- **Property-based (hypothesis)**: sizing invariants, domain/risk properties —
  `test_sizing_properties.py`, `test_domain_properties.py`, `test_risk_properties.py`.
- **No-lookahead gates**: `assert_point_in_time` over the whole feature registry; revised-bar
  leak test (`test_feature_engine.py`).
- **Safety boundaries**: `test_safety_boundaries.py` (live authorization always rejects),
  paper-adapter boundary tests.
- **Integration**: walk-forward periods, phase integration, research runner on
  synthetic universe (`test_research_runner.py`), and the **MVP-1 end-to-end
  acceptance** path (`test_mvp_e2e.py`): vendor CSV → membership builder →
  preflight → fingerprint → run-all families → reports → promotion gate →
  MVP summary, plus every fail-closed negative path (missing/zero-exit
  membership, bad OHLC, unacknowledged warnings, insufficient evidence,
  NO_STRATEGY_PROMOTED-as-success).
- **CLI**: unit coverage for the operator surface — `test_mvp_workflow.py`
  (services/state model/verdicts), `test_dataset_identity.py` (fingerprint
  determinism + tamper detection), `test_cli_safety.py` (legacy `--status`,
  live stays unauthorized, no secrets in diagnostics, CLI imports no
  execution-capable modules).
- **PostgreSQL**: `-m postgres` tests (CI provides the service).
- **Chaos/recovery**: `test_monitoring_recovery.py`, restart/duplicate/heartbeat scenarios.
- **External (gated)**: IBKR paper smoke — never runs without explicit external config.

## Gates (must stay green)

`uv sync --all-extras --locked` | `ruff check` | `ruff format --check` | `mypy` |
`pytest -m "not external"` + coverage >=80% (>=90% on oms/risk/reconciliation/authorization) |
`pip-audit` | gitleaks.

Current (MVP-1 branch): 383 passed, 2 skipped (pg-dependent without
`DATABASE_URL`), 2 deselected (external); coverage 88.57% overall / 94% on the
critical set. PostgreSQL suite: 2 passed against an isolated `postgres:16`
service (migrations idempotency/recovery + shadow round-trip). Quality gates
were not relaxed to achieve this.
