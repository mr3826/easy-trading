# File Ownership Map — integration/remaining-platform-work

Base: verified PR head `37ebec9c9481ebffbf00e284f011a9650199b376` (PR #1, open, unmerged).
Rule: only the orchestrator modifies shared files. Any exception requires a temporary
ownership lock recorded in this file with a reason and expiry (one merge window).

## Orchestrator (exclusive)

| Path | Reason |
|---|---|
| `pyproject.toml`, `uv.lock` | dependency pinning, markers, coverage config |
| `.github/` | CI workflow |
| `README.md` | top-level description |
| `docs/plans/*.md` (this directory, shared docs) | requirement matrix, sequence, ownership, risk register, founder queue |
| `docs/audit/*.md` | phase-status and current-state files |
| `docs/decisions/adr-007-interface-contracts.md` | cross-worker interface contracts |
| `docs/architecture-overview.md`, `docs/threat-model.md`, `docs/test-strategy.md`, `docs/risk-policy.md`, `docs/environment.md` | shared architecture documents |
| `artifacts/` | final verification artifacts |
| `scripts/import_smoke.py`, `scripts/secret_scan.py`, `scripts/generate_verification.py` | shared verification scripts |
| `trading-platform/src/trading_platform/simulator/` | cross-cutting no-lookahead core (unassigned in mission; kept at integration level) |
| `trading-platform/src/trading_platform/authorization.py`, `config.py`, `cli/`, `observability/` | live-authority gate and shared utilities |
| `trading-platform/src/trading_platform/risk__init__.py` | stray module; removal decided at integration |
| `trading-platform/tests/unit/test_safety_boundaries.py`, `test_migrations_and_imports.py`, `test_simulator_safety.py`, `unit/simulator/` | cross-cutting safety and simulator tests |
| root-level scratch files (`debug_*.py`, `fix_*.py`, `mypy_*.txt`, ...) | pre-existing user files, preserved, never committed |

## Agent A — domain and point-in-time market data

| Path | Scope |
|---|---|
| `trading-platform/src/trading_platform/domain/` | all changes |
| `trading-platform/src/trading_platform/data/` (incl. `ingestion/`) | all changes |
| `trading-platform/tests/unit/domain/` | all changes |
| `trading-platform/tests/unit/test_data_pipeline.py` (new) | create |
| `trading-platform/tests/integration/test_data_pipeline_integration.py` (new) | create |
| `docs/data-pipeline.md` (new) | create |

## Agent B — research, experiments and robustness

| Path | Scope |
|---|---|
| `trading-platform/src/trading_platform/strategies/` | all changes |
| `trading-platform/src/trading_platform/walk_forward/` | all changes |
| `trading-platform/src/trading_platform/persistence/experiment.py`, `baseline_report.py` | all changes (PostgreSQL infrastructure excluded) |
| `scripts/walk_forward_first_fold.py` | granted lock (research script) |
| `trading-platform/tests/integration/test_phase4.py`, `test_walk_forward_periods.py` | all changes |
| `trading-platform/tests/unit/test_research_robustness.py` (new) | create |
| `docs/research-methodology.md` (new) | create |

## Agent C — OMS, risk, ledger and reconciliation

| Path | Scope |
|---|---|
| `trading-platform/src/trading_platform/oms/` | all changes |
| `trading-platform/src/trading_platform/risk/` | all changes |
| `trading-platform/src/trading_platform/reconciliation/` | all changes |
| `trading-platform/src/trading_platform/persistence/postgres.py` | all changes |
| `trading-platform/migrations/` | all changes |
| `trading-platform/tests/integration/test_phase6_integration.py`, `test_postgres_store.py` | all changes |
| `trading-platform/tests/unit/test_risk_properties.py` (new) | create (Hypothesis property tests) |
| `trading-platform/tests/unit/test_oms_reconciliation.py` (new) | create |

## Agent D — shadow mode, monitoring, chaos and recovery

| Path | Scope |
|---|---|
| `trading-platform/src/trading_platform/shadow.py` | all changes |
| `trading-platform/src/trading_platform/monitor.py` | all changes |
| `trading-platform/src/trading_platform/chaos_engine.py` | all changes |
| `trading-platform/src/trading_platform/backup.py`, `dead_man.py` | all changes |
| `trading-platform/src/trading_platform/persistence/shadow_archive.py` (new) | create (file-based archival; DB writes go through C's PostgresStore contract) |
| `trading-platform/tests/unit/test_shadow_operation.py` (new) | create |
| `trading-platform/tests/unit/test_monitoring_recovery.py` (new) | create |
| `docs/shadow-operator-guide.md`, `docs/runbooks/backup-restore.md`, `docs/runbooks/incident-response.md`, `docs/alert-setup.md` | operational runbooks granted lock |

## Agent E — IBKR paper adapter

| Path | Scope |
|---|---|
| `trading-platform/src/trading_platform/broker_adapter.py` | all changes |
| `trading-platform/src/trading_platform/broker/` (new package) | create |
| `trading-platform/tests/unit/test_broker_adapter_paper.py` (new) | create |
| `trading-platform/tests/external/test_ibkr_paper_smoke.py` (new) | create (external-marked, skipped by default) |
| `docs/decisions/adr-008-ibkr-client-library.md` | create |
| `docs/ibkr-paper-guide.md` | granted lock (operator documentation) |

## Agent F — ML ranking and LLM feature safety

| Path | Scope |
|---|---|
| `trading-platform/src/trading_platform/ml_pipeline.py`, `ml_ranking.py` | all changes |
| `trading-platform/src/trading_platform/features/` | all changes |
| `trading-platform/tests/unit/test_ml_pipeline_ranking.py` (new) | create |
| `docs/ml-feature-safety.md` (new) | create |

## Cross-worker dependency resolutions (Wave 0)

1. C adds a `shadow_decisions` table to `migrations/` and a `record_shadow_decision()`
   method on `PostgresStore`; D consumes it through a `ShadowDecisionSink` protocol
   declared in `INTERFACE_CONTRACTS.md`. D never edits C's files.
2. E keeps `broker_adapter.py` free of module-level `ib_async` imports (lazy import
   inside the real client only) so default offline tests need no IBKR library.
3. A's `MarketDataProvider`/`ParquetMarketDataProvider` are consumed by B (research)
   and D (shadow completed-bar provider) via the signatures in `INTERFACE_CONTRACTS.md`.
4. C's reconciliation accepts independent `BrokerSnapshot` inputs (domain model) fed by
   E's adapter; no worker compares OMS-derived copies against themselves.
5. Dependency additions (e.g. IBKR client library) are applied centrally by the
   orchestrator; workers record them under `DEPENDENCY_REQUESTS`.
