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

Reconciliation pass (2026-09-25): stale Phases 7–12 checklist reconciled to reality in
[TASK_RECONCILIATION.md](TASK_RECONCILIATION.md); lookahead/commission/PBO/DSR defects in
the research path fixed; `monitoring/paper_evidence.PaperEvidenceTracker` added; PIT
universe-membership filtering supported in the research runner; debug scratch archived to
`archive/legacy-scratch/`.

Data-quality gate (2026-09-26): `research/data_quality.py` + `scripts/run_data_preflight.py`
refuse non-PIT datasets (missing/invalid constituent membership manifest, survivorship
red flags, bar integrity) **before** any strategy research. See
[DATA_PIPELINE.md](DATA_PIPELINE.md).

MVP-1 product surface (2026-09-26): the fragmented operator scripts are unified
under the canonical `trading-platform` CLI (`status`, `doctor`, `data`,
`research`, `report`) with a fail-closed data gate, explicit warning
acknowledgement, dataset fingerprinting, `research run-all` across registered
families, cross-family `MVP_RESEARCH_SUMMARY` artifacts, and a deterministic
product state model. Legacy scripts remain as thin wrappers. See
[MVP1.md](MVP1.md) and [MVP1_READINESS.md](../MVP1_READINESS.md). Still: no
strategy promoted, research evidence requires licensed external data, live
NOT AUTHORIZED.
