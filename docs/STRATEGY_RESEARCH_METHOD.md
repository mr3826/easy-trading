# Strategy Research Method

Canonical flow for every hypothesis (see also `research-methodology.md`):

1. **Register** an explicit hypothesis with fixed upfront parameters and a registered ID
   ([STRATEGY_CATALOG.md](STRATEGY_CATALOG.md)). No iterative tuning against results.
2. **Compute** point-in-time features and regimes; verify no-lookahead via prefix-stability tests.
3. **Backtest** on purged walk-forward OOS windows only ([BACKTEST_VALIDATION.md](BACKTEST_VALIDATION.md)).
4. **Stress**: 1x/1.5x/2x costs, gap-through-stop modeling, time/ATR exit variants as
   separate runs (each is a trial and counted).
5. **Correct** for selection: DSR across all trials, PSR, CSCV PBO, White reality check
   ([OVERFITTING_PROTECTION.md](OVERFITTING_PROTECTION.md)).
6. **Decompose**: regimes, calendar periods, concentration; compare to benchmark + baseline.
7. **Record everything**: `run_family_research` persists JSON + Markdown reports and every
   promotion decision (approvals and rejections) with source commit and dataset hash.
8. **Decide** via the promotion gate ([STRATEGY_PROMOTION_GATE.md](STRATEGY_PROMOTION_GATE.md)).
   REJECTED and RESEARCH_ONLY are normal, valuable outcomes.

Run (requires operator-supplied data — REQUIRES EXTERNAL SETUP):

```
uv run python scripts/run_strategy_research.py --family trend_relative_strength \
    --data-dir <daily-bars> --benchmark SPY --symbols <universe...> \
    --output-dir artifacts/research
```
