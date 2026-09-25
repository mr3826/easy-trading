# Strategy Catalog

## Control / baseline

| ID | Module | Notes |
|---|---|---|
| `ma_cross_<f>_<s>` (5/20 default) | `strategies/ma_cross_strategy.py` | **Unchanged baseline.** Long-only, opposite-signal + max-holding exits. Control for all comparisons. Parameters are provisional and must not be auto-tuned. |

## Candidate families (research)

All candidates: long-only, next-open entries, ATR stops, regime+liquidity filters,
`SignalEvidence` output. Registered in `strategies/candidates.py::STRATEGY_FAMILIES`.

| Family | ID pattern | Hypothesis | Key params |
|---|---|---|---|
| A — Trend + Relative Strength | `trend_rs_m{63,126}_rs{63,126}_atr2.5` | In an allowed market regime, stocks with positive medium-term momentum and positive relative strength vs benchmark continue outperforming short-horizon. | momentum_window, rs_window, trend_ma_window, allowed regimes |
| B — Breakout + Volume | `breakout_volume_b{20,55}_rv{1.5,2.0}` | N-day Donchian breakouts confirmed by elevated relative volume follow through. | breakout_window, min_relative_volume |
| C — Trend Pullback | `trend_pullback_ma200_rsi{3,5}_{20,30}` | In long-term uptrends, short-term RSI oversold + recovery trigger offers better entries. | long/medium MA windows, RSI window, oversold/recovery thresholds |
| D — Baseline | `ma_cross_5_20` | Control. | fixed |

## Rules

- Each family is registered with an ID and must carry **independent evidence** — families are
  never merged into one optimized rule set.
- Candidate generation operates over the configured liquid universe, never hand-picked winners.
- A strategy only reaches paper execution with a current `APPROVED` promotion artifact
  pinned to its exact strategy version (see [STRATEGY_PROMOTION_GATE.md](STRATEGY_PROMOTION_GATE.md)).
- Exit-set variants (opposite signal / ATR initial / ATR trailing / Donchian trail / time stop /
  profit protection) are evaluated individually or in limited, pre-declared combinations.
