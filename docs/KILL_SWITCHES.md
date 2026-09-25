# Kill Switches

Module: `risk/kill_switch.py` (`KillSwitchCoordinator`) — **IMPLEMENTED, TESTED, CI-VERIFIED**
(>=90% coverage under risk/ enforced by CI).

## Blocking conditions (any one blocks NEW exposure; unknown = blocked)

- stale market data (age beyond policy) / expected completed bar missing
- clock uncertainty (skew beyond tolerance)
- database unavailable / broker unavailable
- reconciliation mismatch / unknown broker position / duplicate-order uncertainty
- daily loss breach / portfolio drawdown breach / gross-exposure breach
- broker reject storm (too many rejects)
- monitoring heartbeat stale (dead man)
- market closed (session/calendar awareness)
- risk engine already blocking / strategy unapproved / drift monitor DISABLED
- manual operator latch (`latch`/`release`)

## Semantics

- `BlockDecision.allow_new_exposure=False` → reject new entries. Existing positions are
  untouched. Cancel-open-orders and liquidation are separate explicit operations.
- Fail closed: any input that is None/unknown is treated as a breach (tested).
- Latches are deliberate, named, and individually releasable — no hidden global overrides.
