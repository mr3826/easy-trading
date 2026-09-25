# Implementation Report — Alpha Validation, Strategy Reverification & Autonomous Paper Execution

Date: 2026-09-25. Branch: `feature/alpha-validation-autonomous-paper`.

## 1. Repository baseline

- Base commit `c333219476555930578b1643791f56befb8f7c30` (`main`), fetched + fast-forwarded,
  working tree clean. Pre-existing uncommitted debug-script edits on
  `fix/verified-platform-foundation` were stashed (kept, not destroyed).
- Baseline verified: 228 tests green, ruff/format/mypy/pip-audit/gitleaks CI, platform
  modules as listed in `docs/current-state-audit.md`.
- Audit output: `docs/current-state-audit.md` (what exists / partial / simulated /
  external / evidence ceilings).

## 2. Architecture changes

Purely additive on top of the existing modular architecture; no component was rewritten or
duplicated. New packages: `features/indicators.py`, `regimes/`, `trade_planning/`,
`validation/`, `promotion/`, `research/`, `risk/kill_switch.py`, `orchestration/`,
`monitoring/drift.py`, `strategies/candidates.py`. No Kafka/Kubernetes/Redis or other
infrastructure additions. Existing OMS, risk engine, reconciliation, shadow, IBKR adapter,
monitoring, persistence, and walk-forward were reused as-is.

## 3. New feature system

Broker-independent point-in-time indicators (`trading_platform.features.indicators`):
SMA, EMA, RSI, ATR, normalized ATR, realized vol, returns/ROC, rolling high/low,
Donchian (current-bar-excluded), volume SMA, relative volume, volume z-score, distance
from MA, rolling drawdown, relative strength/beta/correlation vs benchmark, gap %,
price percentile, volatility percentile, 52w-high distance. Each has a versioned
`FeatureSpec` + fingerprint. No-lookahead is enforced by prefix-stability tests over the
entire registry and a revised-bar leak test.

## 4. New strategy hypotheses

- A: Trend + Relative Strength (`trend_rs_*`)
- B: Breakout + Volume Confirmation (`breakout_volume_*`)
- C: Trend Pullback (`trend_pullback_*`)
- D: MA cross 5/20 preserved untouched as the control/baseline.

Each family emits `SignalEvidence` with regime, confirmations, feature hash, planned
entry/stop, score, and explicit rejection reasons. Eligibility is strictly separated from
score-based ranking (tested that score cannot redeem ineligible signals).

## 5. Validation / reverification system

`validation/`: purged/embargoed walk-forward splits (anchored or rolling, leakage-verified),
PSR, DSR (with `n_trials` across the whole family), CSCV PBO, Politis-Romano stationary
bootstrap, White reality check, parameter-stability surface, cost stress (1x/1.5x/2x),
regime decomposition, concentration (top-1/top-5, trades and symbols), benchmark
comparison. `research/runner.py` executes the full pipeline per family and persists every
trial's evidence + promotion decision.

## 6. Overfitting protections

Family-level trial counting (failures included), DSR, PSR, PBO, White RC, stability
surface, 2x cost survival requirement, concentration flags, survivorship/universe-bias
ceiling labels in every report. See `docs/OVERFITTING_PROTECTION.md`.

## 7. Position-sizing changes

`trade_planning.size_position`: floor(equity*risk_fraction / risk_per_share) then clamps
for cash/reserve, position notional, gross exposure, sector cap, max positions, hard cap;
binding constraint reported. One-share diagnostic mode remains at the simulator level.
Kelly excluded from promotion by default. Property-based invariants tested.

## 8. Execution integration

`orchestration.PaperOrchestrator`: deterministic decision IDs, append-only fsync journal,
restart idempotency, preflight kill-switch (approval + drift + health), eligibility ->
ranking -> sizing -> hard risk -> submit -> journal, reconciliation-health gate before
exposure. Paper only; imports no live authority.

## 9. Kill-switch behavior

`risk/kill_switch.py`: aggregated blocking for stale data, missing bar, clock skew, DB/
broker down, reconciliation mismatch, unknown broker position, duplicate-order uncertainty,
daily-loss/drawdown/exposure breach, reject storm, heartbeat, market closed, risk block,
unapproved strategy, drift-disabled, operator latches. Blocks affect NEW exposure only —
never liquidation; unknown = blocked.

## 10. Drift monitoring

`monitoring/drift.py`: HEALTHY/WATCH/DEGRADED/DISABLED vs frozen promotion expectations,
with early-life guard and manual disable. DISABLED blocks new positions through the
orchestrator. No self-retuning.

## 11. Tests

Added: feature engine (correctness/warmup/no-lookahead), regimes, trade planning, strategy
candidates, gap-through-stop backtest, reverification stats, promotion gate, orchestrator,
kill switches, drift, sizing property tests, and a synthetic end-to-end research-runner
integration test. Total suite: **321 passed, 2 skipped (PostgreSQL-service), 2 deselected
(external IBKR)** vs 228 at baseline (see §21 for the reconciliation run). No existing test
weakened.

## 12. Coverage

88% total (`--cov-fail-under=80` gate satisfied); new critical-path `risk/kill_switch.py`
at 95% (>=90% critical gate satisfied along with oms/risk/reconciliation/authorization).

## 13. Strategy research results

No real universe data exists in-repo (REQUIRES EXTERNAL SETUP), so no market-truth claims
are made. The full pipeline is exercised on a synthetic universe in
`tests/integration/test_research_runner.py`: all trials recorded, per-config evidence,
promotion decisions persisted, biases/ceiling labeled. To produce the real report:

```
uv run python scripts/run_strategy_research.py --family <family> \
    --data-dir <daily-bars> --benchmark SPY --symbols <universe...> \
    --output-dir artifacts/research
```

## 14. Strategies rejected and WHY

None yet — rejection requires real-data runs. The machinery records REJECTED/RESEARCH_ONLY
with explicit reasons whenever evidence is insufficient (unit-tested).

## 15. Strategy(s) approved for shadow/paper

**None.** No promotion artifact is claimed: there is no real-data evidence yet. The
orchestrator therefore currently blocks all cycles (fail closed) — observed behavior in tests.

## 16. External gates

- IBKR paper smoke test: `REQUIRES_EXTERNAL_SETUP` (no credentials present here).
  Never faked. Operator commands in `docs/IBKR_PAPER_OPERATION.md`.
- PostgreSQL integration tests run in CI service (2 skipped locally).
- Real universe data + point-in-time constituents: required before any promotion decision
  means anything.

## 17. Known limitations

See `docs/LIMITATIONS.md`: universe bias ceiling, daily-bar modeled fills, modeled costs,
small-sample statistics, no forward record.

## 18. Security findings

- Live-authorization boundary verified fail-closed (existing tests retained and passing).
- No credentials touched or required by new code; secret scan clean
  (`scripts/secret_scan.py`).
- New code introduces no broker side effects; research runner is read-only.
- `pip-audit`: no vulnerabilities (local package not on PyPI is the only skip note).

## 19. Remaining risks

- Real-data performance is unknown by definition; synthetic tests prove machinery, not edge.
- Research backtester is deliberately simpler than the event-driven simulator; both remain
  available and divergences would be reconciled during shadow operation.
- Promotion policy thresholds are conservative defaults and explicitly policy choices.

## 20. Exact next operational step

Provide a point-in-time daily-bars universe (parquet, e.g. via the existing ingestion path)
plus a benchmark, then run `scripts/run_strategy_research.py` per family, review the
generated report, and allow the promotion gate to decide. If a family is APPROVED, start
shadow operation (`shadow-operator-guide.md`) to accumulate forward evidence before any
autonomous paper session.

## 21. Addendum — reconciliation run (2026-09-25)

Full task-source audit reconciled the stale Phases 7–12 checklist against actual code and
tests: see `docs/TASK_RECONCILIATION.md` (authoritative) and the rewritten
`remaining_todos.md` ([x]/[~]/[ ] legend). Genuine defects found and fixed during this pass:

1. Research backtester trailing-stop used the **same bar's close/ATR** to set a stop and
   evaluate that same bar's low — a lookahead. Fixed: exits use the stop knowable at the
   bar open; regression test added.
2. Research entry sizing priced risk against the stale signal close and ignored entry
   commission; now sized at the actual fill with commission + 20% notional cap (parity
   with production `SizingPolicy`).
3. Production `size_position` cash clamp did not reserve the entry commission (could
   overdraw settled cash at the boundary). Fixed with unit + property tests.
4. DSR fallback selection-bias scale had a units error for annualized Sharpe. Fixed;
   regression test.
5. PBO could crash on thin data or over-trust 2-row blocks. `effective_pbo_blocks`
   (≥5 rows/block) + fail-closed `INSUFFICIENT_DATA` reporting added; promotion then
   rejects on missing PBO evidence.
6. Orchestrator re-checks the kill switch and the promotion artifact immediately before
   every submission (mid-cycle operator latch / approval deletion now fail closed, tested).
7. Research runner promotion artifacts no longer silently write to the CWD
   (explicit `promotions_root`, tested).

Completed previously-existing capability gaps: `PaperEvidenceTracker`
(`monitoring/paper_evidence.py`) makes all Phase 10 evidence questions automatically
answerable from real events; point-in-time universe membership filtering added to the
research path so the survivorship ceiling is only removed by real PIT membership data;
CI now triggers on `feature/*` pushes; obsolete tracked debug scripts moved to
`archive/legacy-scratch/` with provenance notes; implemented ADRs re-marked Accepted.

Verification after reconciliation: ruff check/format clean, mypy clean (53 files),
321 passed / 2 skipped / 2 deselected, coverage 88%, critical-path coverage 94%,
secret scan clean, pip-audit clean.
