# Limitations

Honest constraints on what the current evidence can support:

1. **Universe bias**: research universe is not point-in-time index membership; delisted
   names are absent. All backtests are `CAPPED` until this is fixed.
2. **Daily bars only** (ADR-001): intraday fills, spreads, and queue dynamics are modeled,
   not observed. Gap handling is modeled conservatively but still modeled.
3. **Cost model**: fixed commission + slippage fraction (stressed to 2x). Real paper fills
   may differ; forward evidence must confirm slippage estimates.
4. **Small universes in tests**: synthetic-data tests validate machinery, not alpha. Code
   existence is never market evidence.
5. **Small sample sizes**: daily data over a few years yields limited independent bets;
   DSR/PBO corrections apply but cannot manufacture power.
6. **Regime history depth**: vol/trend percentiles need long windows; early data is
   classed conservatively (neutral/unacceptable).
7. **No forward track record**: no strategy has shadow/paper history; everything is
   REQUIRES FORWARD EVIDENCE.
8. **Live trading**: NOT AUTHORIZED and permanently fail-closed in code.
