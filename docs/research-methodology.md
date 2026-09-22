# Research Methodology

## What is proven (reproducible, verifiable)

1. **Chronological isolation.** Train/validation/test periods are strictly
   chronological with no overlap and no shuffling. Tests poison validation
   and test bars and assert training metrics are unchanged; train decisions
   never see validation or test data.
2. **Point-in-time signal generation (no lookahead).** Every decision is
   generated with an as-of bound: only bars with `timestamp <= as_of` may
   influence it, and the SMA window is the last `slow_length` bars available
   at or before the as-of time. Tests construct datasets where future bars
   would flip the signal and assert the signal does not change.
3. **Deterministic simulation.** Identical bars, signals and simulator
   configuration produce identical final cash and trade ledgers (verified by
   re-running phases and comparing ledgers per fold).
4. **Experiment reproducibility.** Experiment records persist the dataset
   hash, the full git HEAD SHA, the dependency-lock hash and the policy
   hash; malformed records are rejected, not silently skipped.
5. **Robustness machinery.** Parameter-stability analysis (perturbed
   fast/slow lengths), cost and slippage sensitivity (commission multipliers
   and documented post-hoc slippage estimates), turnover/drawdown/exposure
   analysis, and a trade-level bootstrap drawdown distribution are
   implemented and tested.

## What is NOT proven (never claimed)

1. **Strategy profitability.** The MA-crossover hypothesis has PROVISIONAL
   parameters. No profitability claim is made without reproducible,
   out-of-sample evidence; a passing engineering baseline is platform
   correctness only.
2. **Out-of-sample validity.** The locked test window has not been observed
   in forward time. Walk-forward folds re-evaluate overlapping test windows
   (multiple-testing caveat; Bonferroni adjustment is applied in
   `aggregate_results()`), which does not create new out-of-sample data.
3. **Live or paper performance.** No broker connection, no order submission,
   no paper duration has occurred as part of repository verification.
4. **Bootstrap as a forecast.** The bootstrap drawdown distribution describes
   the resampled historical trade population; it is not a forward-looking
   risk forecast.

## Documented V1 simplifications

- Multi-symbol walk-forward runs symbols through chained per-symbol
  simulations (portfolio carried in chronological symbol order); position
  limits bind across symbols via the simulator's internal risk engine.
- Signal generation tracks positions per symbol with a lightweight state
  machine; a BUY that expires unfilled creates a phantom-long in the
  signal-generation state only — the simulator state is authoritative.
- Slippage sensitivity is estimated post-hoc (fill notional × slippage_pct ×
  multiplier) because MARKET fills with `price=None` record zero realized
  slippage.
- Position market values are marked at fill price and not re-marked to later
  bars; equity curves built from portfolio snapshots approximate economic
  equity.
