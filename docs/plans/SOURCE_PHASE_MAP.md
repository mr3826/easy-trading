# Source-to-Phase Map — integration/remaining-platform-work

Maps production sources to phases with audited state at base `37ebec9`.

| Phase (per phase-gap-matrix) | Sources | Audited state at base |
|---|---|---|
| 0 Charter/policy | `docs/product-charter.md`, `docs/risk-policy.md`, `docs/decisions/` | Present; risk values PROVISIONAL |
| 1 Domain | `domain/__init__.py` | Validation solid; value objects mutable; no property tests |
| 2 Data | `data/__init__.py`, `data/ingestion/daily_bar_ingestion.py` | `get_bars` returns `[]` (TODO); placeholder metadata/checksum; ingestion unreachable from src |
| 3 Simulator | `simulator/event_driven_simulator.py` | Signal deferral correct; MARKET same-bar-close reachable; self-contained (no OMS call) |
| 4 Research | `strategies/ma_cross_strategy.py`, `persistence/baseline_report.py`, `persistence/experiment.py` | `compute_code_hash` placeholder; report placeholders (`max_drawdown/turnover/seed: None`); broken `realized_pnl` contract |
| 5 Walk-forward | `walk_forward/walk_forward.py` | Future-bar signal generation; signal/bar misalignment; buggy bootstrap; `_check_determinism` stub |
| 6 OMS/risk/reconciliation/Postgres | `oms/oms.py`, `risk/`, `reconciliation/`, `persistence/postgres.py`, `migrations/0001_core.sql` | Placeholder commissions; stub fill reconciliation; copied-state snapshots; idempotency hole; DB layer real (append-only trigger, unique constraints, transactional transitions) |
| 7 Chaos/backup | `chaos_engine.py`, `backup.py` | Backup encryption real; chaos injects only into wrapped callables; FAILURE_DISK_PRESSURE no-op |
| 8 Shadow/monitor | `shadow.py`, `monitor.py`, `dead_man.py` | shadow.py 23-stmt sink; monitor checks caller-fed; dead_man CLI real |
| 9 Broker boundary | `broker_adapter.py` | Fake/IBKR-paper boundaries; no client library; one flag; Alpaca skeleton placeholder |
| 10 Paper validation | `risk_engine.py` (paper-session checks) | Offline checks only; sixty days have not occurred |
| 11 ML/LLM | `ml_pipeline.py`, `ml_ranking.py`, `features/` | Chronological split, closed-form fit, hashes real; LLM timeouts missing; promotion data-only |
| 12 Authorization | `authorization.py`, `config.py` | Fail-closed permanently; flags neutralized |

## Unassigned sources (orchestrator-owned)

| Source | Reason |
|---|---|
| `simulator/` | cross-cutting no-lookahead core |
| `authorization.py`, `config.py`, `cli/`, `observability/` | live-authority gate and shared utilities |
| `risk__init__.py` | stray empty module (removed at integration) |
| `scripts/import_smoke.py`, `secret_scan.py`, `generate_verification.py` | shared verification |
