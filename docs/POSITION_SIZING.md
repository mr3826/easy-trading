# Position Sizing

Module: `trading_platform.trade_planning` — **IMPLEMENTED, TESTED, CI-VERIFIED**
(including hypothesis property tests over sizing invariants).

## Production research sizing (risk-based)

```
risk_budget      = equity * risk_fraction            (default 1%)
risk_per_share   = estimated_entry - initial_stop    (ATR stop)
shares           = floor(risk_budget / risk_per_share)
```

then clamped by, in binding order reported by `SizingResult.binding_constraint`:

1. settled cash minus minimum cash reserve (5%)
2. max position notional (20% of equity)
3. portfolio gross exposure room (90% cap)
4. sector concentration cap (optional, per call)
5. maximum positions (3)
6. hard quantity cap (100,000)

All values are versioned `SizingPolicy` **policy choices**. The one-share simulator mode
remains available for diagnostics but is no longer the meaningful research model.

Kelly sizing is intentionally not used for promotion (fractional risk-of-ruin sizing is the
default); any Kelly analysis is research/report-only.
