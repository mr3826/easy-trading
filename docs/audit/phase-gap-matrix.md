# Phase Gap Matrix

Statuses use the required ceiling. A `CI_VERIFIED` entry is supported by the
named source, test, command, and committed evidence bundle. It does not imply
external connectivity, forward validation, or strategy profitability.

| Phase | Status | Source implementation | Meaningful tests and command | Evidence |
|---:|---|---|---|---|
| 0 | `IMPLEMENTED_UNVERIFIED` | `docs/product-charter.md`, `docs/risk-policy.md`, `docs/decisions/`, original phase plan | Documentation review; `uv run python scripts/generate_verification.py` | `artifacts/verification/verification-summary.md`; operational approval is external |
| 1 | `CI_VERIFIED` | `trading-platform/src/trading_platform/domain/__init__.py` | `tests/unit/domain/test_domain.py`; `uv run pytest -m "not external"` | `artifacts/verification/test-results.xml`; source commit recorded in summary |
| 2 | `CI_VERIFIED` | `data/__init__.py`, `data/ingestion/daily_bar_ingestion.py` | `test_operational_depth.py::test_data_validation_and_ingestion`; same suite command | Coverage XML and test results; real vendor operation remains external |
| 3 | `CI_VERIFIED` | `simulator/event_driven_simulator.py` | `test_simulator_safety.py`; `uv run pytest -m "not external"` | `no-lookahead-test.txt`, coverage XML, CI run |
| 4 | `CI_VERIFIED` | `strategies/ma_cross_strategy.py`, `persistence/baseline_report.py`, `persistence/experiment.py` | `test_phase4.py`, operational depth report tests | `test-results.xml`, `coverage-summary.md`; no profitability claim |
| 5 | `PARTIAL` | `walk_forward/walk_forward.py`, ML evaluation modules | walk-forward period tests and deterministic ML tests | Offline code is tested; robustness and multiple-testing evidence remain incomplete |
| 6 | `CI_VERIFIED` | `persistence/postgres.py`, `migrations/0001_core.sql`, `oms/`, `risk/`, `reconciliation/` | `test_postgres_store.py` against CI PostgreSQL; `uv run pytest -m postgres` | `migration-test.txt`, `restart-recovery-test.txt`, CI PostgreSQL job |
| 7 | `CI_VERIFIED` | `chaos_engine.py`, `backup.py` | dependency-fault, checksum, encryption, recovery tests | `test-results.xml`, coverage XML; operational restore drill is not claimed |
| 8 | `PARTIAL` | `shadow.py`, `monitor.py`, `dead_man.py` | shadow isolation, alert transport, and dead-man tests | `shadow-isolation-test.txt`; forward operation remains pending |
| 9 | `REQUIRES_EXTERNAL_SETUP` | `broker_adapter.py` (`FakeBrokerAdapter`, `ShadowBrokerAdapter`, `IBKRPaperBrokerAdapter`) | fake boundary and paper-only rejection tests | `test-results.xml`; no IBKR connection was attempted |
| 10 | `REQUIRES_FORWARD_EVIDENCE` | paper-validation framework in `risk_engine.py` | offline validation tests only | `external-gates.md`; sixty trading days have not occurred |
| 11 | `PARTIAL` | `ml_pipeline.py`, `ml_ranking.py`, `features/` | deterministic split/hash/fallback and malformed-output tests | coverage/test artifacts; promotion and LLM forward performance are unproven |
| 12 | `NOT_AUTHORIZED` | `authorization.py`, `config.py` | authorization expiry/reconciliation/default tests | `no-live-authority-test.txt`; evaluator permanently returns disabled |
