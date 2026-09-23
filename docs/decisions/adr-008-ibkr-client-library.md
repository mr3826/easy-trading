# ADR-008: IBKR Client Library

## Status

Accepted (Wave 2, Agent E)

## Context

`IBKRPaperBrokerAdapter` needs a TWS/Gateway API client to implement the paper
connection lifecycle: connect/reconnect, next-valid-order-ID tracking, order
submission and acknowledgement, status updates, cancellation, replacement,
partial fills, commissions, rejections, parent/child protective orders, OCA
behavior, and retrieval of account/cash/positions/open/completed orders.

Constraints from ADR-006/ADR-007 and contract C5:

- The client library is pinned centrally in `pyproject.toml` by the
  orchestrator (currently `ibkr = ["ib_async>=1.0.3"]`); workers must not edit
  that file.
- `broker_adapter.py` must not import the client library at module level;
  default offline tests must pass without the library installed.
- No credentials, no external connections, and no orders are permitted as part
  of repository verification.

## Decision

Use **`ib_async`** as the IBKR TWS/Gateway API client library, imported lazily
inside the client module (`trading_platform/broker/ibkr_paper.py`), targeting
the 2.x API (installed resolution is `ib-async==2.1.0`).

ib_async is the maintained continuation of `ib_insync` by the original
upstream maintainers (Ewald de Wit), restructured around asyncio with the same
event-driven object model (`IB`, `Trade`, `OrderStatus`, `Contract`, fill and
commission report events) that fits the adapter's callback-driven ledger.

### Alternatives considered

1. **Official `ibapi`** (Interactive Brokers): lower-level, callback-handler
   based (EClient/EWrapper), requires manual event loop management, manual
   object decoding, and manual state tracking. More control, but
   substantially more adapter code for the same behavior and a larger surface
   for defects. Rejected for V1.
2. **`ib_insync`**: the historical predecessor. Upstream development stopped
   and the project moved to `ib_async`; depending on it would pin the platform
   to unmaintained code. Rejected.
3. **Raw socket implementation**: implementing the TWS wire protocol directly.
   Maximum control, unacceptable maintenance and correctness burden for a
   paper-trading integration. Rejected.

If a strongly preferable alternative appears later, `pyproject.toml` is NOT
changed by workers; the need is recorded under `DEPENDENCY_REQUESTS` and the
adapter keeps a thin wrapper so only `broker/ibkr_paper.py` changes.

## Consequences

- `broker_adapter.py` and `trading_platform/broker/__init__.py` stay free of
  module-level `ib_async` imports; the lazy import inside `ibkr_paper.py`
  means default offline tests never need the library.
- The adapter's connection state comes only from the client handshake
  (`ib.isConnected()`) exposed via a read-only property; simulated
  `connected=True` attribute assignment from outside cannot enable submission.
- Two-flag gate: `allow_connection` permits connect; `allow_paper_orders`
  permits paper-order submission; `execute_order` raises unless both are set.
- Known live ports (7496 TWS live, 4001 Gateway live) and live account
  configuration are rejected in the adapter and in the external smoke harness;
  paper ports are 7497 (TWS) / 4002 (Gateway) and paper accounts follow the
  `DUxxxxxxx` convention.
- Adapter state (order ledger, account/position caches) is maintained
  independently from OMS state; `sync_state` never seeds the ledger from OMS
  orders, fixing the copied-state violation of the previous boundary.

## Related ADRs

- ADR-006: Execution adapter pattern
- ADR-007: Cross-worker interface contracts
