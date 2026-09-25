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
- **Integration**: walk-forward periods, phase integration, research runner on synthetic
  universe (`test_research_runner.py`).
- **PostgreSQL**: `-m postgres` tests (CI provides the service).
- **Chaos/recovery**: `test_monitoring_recovery.py`, restart/duplicate/heartbeat scenarios.
- **External (gated)**: IBKR paper smoke — never runs without explicit external config.

## Gates (must stay green)

`uv sync --all-extras --locked` | `ruff check` | `ruff format --check` | `mypy` |
`pytest -m "not external"` + coverage >=80% (>=90% on oms/risk/reconciliation/authorization) |
`pip-audit` | gitleaks.

Current: 306 passed, 2 skipped (pg-dependent), 2 deselected (external). Quality gates were
not relaxed to achieve this.
