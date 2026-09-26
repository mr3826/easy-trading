# Failure Modes

Chaos coverage: `chaos_engine.py` — **IMPLEMENTED, TESTED**. See also
[runbooks/incident-response.md](runbooks/incident-response.md).

| Failure | Detection | Behavior |
|---|---|---|
| Internet/DB loss | probes, heartbeat | block new exposure; alert; recoverable via `FailureInjector.recover` semantics in tests |
| Stale/quote deviation | freshness check | block; no orders from stale data |
| Duplicate event | idempotency keys, ledger | deduped; uncertainty blocks |
| Broker rejection storm | reject counter | block after threshold |
| Restart mid-order | ledger replay | rebuild state; no duplicate decisions (journal) |
| Clock skew | skew input | block |
| Partial/late fills | OMS + incidents | tracked, reconciled, alerted |
| Reconciliation mismatch | fail-closed chain | block until operator resolves |
| Corrupted approval artifact | policy hash/version check | treated as unapproved => blocked |

Design rule: failures **block new exposure first**; liquidation is never an automatic
consequence of an operational fault.
