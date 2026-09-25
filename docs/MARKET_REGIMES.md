# Market Regimes

Engine: `trading_platform.regimes` — **IMPLEMENTED, TESTED, CI-VERIFIED**. Fully deterministic;
no LLM, no ML.

## Axes

| Axis | Values | Inputs (point-in-time) |
|---|---|---|
| Trend | bullish / bearish / neutral | benchmark close vs 200d SMA (2% neutral band) + 20d MA slope |
| Volatility | low / normal / high / extreme | 20d realized-vol percentile within trailing 252d |
| Liquidity | acceptable / unacceptable | 20d median dollar volume vs configured floor |

Thresholds live in `RegimePolicy` (versioned). They are **policy choices**, not truths.

## Rules of use

- Regimes **filter or select** strategy families (e.g., trend candidates allowed in
  bullish/neutral trend; mean-reversion disabled in strong trends unless separately validated).
- Regimes **never loosen hard-risk constraints**.
- Warmup rows classify as `neutral`/`unacceptable` — meaning "no evidence", which strategies
  treat as rejection, not permission.
- Whether regime filtering improves results is an empirical question answered per family by
  the reverification engine (regime decomposition + walk-forward), never assumed.
