# Current State

Authoritative snapshots:

- [current-state-audit.md](current-state-audit.md) — verified audit at this branch's base
  (HEAD `c333219`): what is IMPLEMENTED / TESTED / CI-VERIFIED / SIMULATED /
  REQUIRES EXTERNAL SETUP / NOT AUTHORIZED.
- [audit/current-state.md](audit/current-state.md) — prior platform audit.

## This branch adds (all IMPLEMENTED, TESTED at unit/integration level)

- `features/indicators.py` — point-in-time feature engine (24 indicators + registry).
- `regimes/` — deterministic regime classification.
- `strategies/candidates.py` — strategy families A/B/C (MA-cross preserved as baseline D).
- `trade_planning/` — SignalEvidence, TradePlan, risk-based sizing.
- `validation/` — purged/embargoed splits, PSR/DSR, CSCV PBO, stationary bootstrap,
  White reality check, concentration, regime decomposition, benchmarking, cost stress.
- `promotion/` — versioned promotion gate with immutable artifacts.
- `research/` — gap-aware research backtester, family runner, JSON+Markdown reports.
- `risk/kill_switch.py` — fail-closed kill-switch coordinator.
- `orchestration/` — autonomous paper orchestrator (journaled, idempotent, gated).
- `monitoring/drift.py` — strategy drift monitor (HEALTHY/WATCH/DEGRADED/DISABLED).

All strategy conclusions remain **REQUIRES FORWARD EVIDENCE** / **REQUIRES EXTERNAL SETUP**
(real universe data). Live trading remains **NOT AUTHORIZED**.
