# Backtest Validation

Validation framework: `trading_platform.validation` + `research/`.
**IMPLEMENTED, TESTED** (synthetic + unit); real-data runs are **REQUIRES EXTERNAL SETUP**.

## Method

1. **Purged walk-forward** (`purged_walk_forward_splits`): contiguous folds, anchored or rolling.
   No shuffling, ever. An `embargo` >= max holding period separates train from test, purging
   any training observation whose outcome window could overlap the test period.
2. **OOS evaluation**: strategies are evaluated only on concatenated untouched test windows.
3. **Parameter stability**: L1-neighborhood median of the best config's metric must stay within
   tolerance — isolated optima are rejected (`parameter_stability_surface`).
4. **Transaction-cost stress**: 1.0x / 1.5x / 2.0x commission+slippage re-runs
   (`COST_STRESS_MULTIPLIERS`); promotion requires positive expectancy at 2x.
5. **Fills**: stop prices are *not* guaranteed. Bars opening through a stop fill at the open
   (gap loss realized); modeled, tested (`test_backtest_gap_through_stop...`).
6. **Benchmarking**: every report includes cash (implicitly), buy-and-hold benchmark, and the
   MA-cross baseline; excess return, beta, drawdown and Sharpe differences are reported
   (`benchmark_comparison`).
7. **Regime decomposition**: per-regime Sharpe/return/maxDD so "a bull market did it" is visible.
8. **Concentration**: top-1/top-5 share of positive P&L by trade and by symbol; outlier-driven
   results are flagged and rejected (`concentration`).
9. **Survivorship audit**: runs flag `evidence_ceiling=CAPPED` whenever the universe is not
   point-in-time constituent membership. Non-PIT results may never be called unbiased.
