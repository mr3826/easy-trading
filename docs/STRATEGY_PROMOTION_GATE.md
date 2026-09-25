# Strategy Promotion Gate

Module: `trading_platform.promotion` — **IMPLEMENTED, TESTED, CI-VERIFIED**.

A strategy is paper-execution eligible **only** with a current `APPROVED` artifact.

## Policy (versioned: `PROMOTION_POLICY_VERSION`, hash-pinned)

All thresholds are **POLICY CHOICES**, not mathematical truths (`PromotionPolicy`):

| Evidence class | Default requirement |
|---|---|
| Sample | trade_count >= 30 |
| Performance | expectancy > 0; Sharpe > 0; maxDD > -35%; profit factor > 1.0 |
| Significance | PSR >= 0.95; DSR >= 0.90 (deflated for all trials) |
| Overfitting | PBO < 0.5 (required evidence) |
| Consistency | >= 50% of walk-forward folds positive |
| Cost stress | expectancy positive at 2x costs |
| Concentration | top-1 trade/symbol <= 50% of positive P&L |
| Benchmark | excess return > 0 vs buy-and-hold |
| Stability | best config must sit in a stable parameter neighborhood |

No single metric can promote a strategy; all independent evidence classes must pass.
Missing evidence fails closed (REJECTED).

## Artifacts

- Decisions persisted as immutable JSON under `TRADING_PROMOTIONS_ROOT`
  (default `artifacts/promotions/<strategy_id>/<config>--<hash>.json`).
- Statuses: `APPROVED` / `REJECTED` / `RESEARCH_ONLY`, always with explicit reasons.
- Approvals are pinned to the **current policy version + hash** and the exact strategy
  version. Changing the policy invalidates prior approvals — revalidation is mandatory.
- The paper orchestrator refuses to run without a current matching artifact.
