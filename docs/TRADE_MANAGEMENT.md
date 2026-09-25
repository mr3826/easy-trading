# Trade Management (Exits)

Research exits are compared, never blindly combined. Research engine:
`research/backtest.py` — **IMPLEMENTED, TESTED (incl. gap stress)**.

## Exit variants under study

| Exit | Semantics |
|---|---|
| Opposite signal | strategy-dependent control (baseline MA-cross uses this) |
| ATR initial stop | `entry - k*ATR` |
| ATR trailing stop | ratchets with highest close since entry |
| Donchian/trend trailing | channel-based trailing (candidate parameterization) |
| Time stop | `max_holding_days` |
| Profit protection | guard rail variant; evaluated only as a declared combination |

## Gap realism (critical)

A stop price is **not a guaranteed fill**. If a bar opens through the stop, the fill is the
open minus slippage (gap loss realized). Modeled and tested; overnight gap stress is part of
cost/robustness evaluation. Stop-fill slippage scales with the cost-stress multiplier.
