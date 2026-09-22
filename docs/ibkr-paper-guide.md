# IBKR Paper Boundary Guide

## Current Status

`IBKRPaperBrokerAdapter` (in `trading-platform/src/trading_platform/broker/ibkr_paper.py`)
implements the real IBKR paper client against the `ib_async` library (ADR-008,
pinned centrally in `pyproject.toml` under the `ibkr` extra). The implementation
is fully gated: no connection and no order submission can occur without two
explicit flags, and no account, credential, or order is included here. No IBKR
connection has occurred as part of repository verification.

## Two-Flag Gate

- `allow_connection=False` (default): `start()` refuses to connect until this
  flag is set. Connection state comes only from the client handshake
  (`ib.isConnected()`), exposed via a read-only property; assigning
  `adapter.connected = True` from outside raises AttributeError and can never
  enable submission.
- `allow_paper_orders=False` (default): `execute_order()` and
  `execute_bracket_order()` raise RuntimeError unless this flag is ALSO set and
  the handshake is established. Both flags must be set for any submission.

Live ports (7496 TWS live, 4001 IB Gateway live) and live account configuration
(accounts explicitly marked live, or any account string that is not the
`DUxxxxxxx` paper convention) are rejected with ValueError in the adapter
constructor and in the external smoke harness. Paper ports are 7497 (TWS) and
4002 (Gateway). The adapter never holds credentials.

## Lifecycle Contract

Implemented in `broker/ibkr_paper.py`, against the ib_async 2.x API:

- Connection lifecycle: `start()` validates the target, lazily imports
  `ib_async`, registers the instance event handlers once, and connects
  (`IB.connect(host, port, clientId, timeout, readonly, account)`); `stop()`
  disconnects idempotently; `reconnect()` re-establishes the handshake and the
  adapter-owned order ledger survives stop/reconnect.
- Next-valid-order-ID tracking: order ids are allocated via
  `ib.client.getReqId()` and tracked on the adapter
  (`next_valid_order_id` property); bracket legs consume ids inside
  `ib.bracketOrder` and are tracked from the returned legs.
- Submission and acknowledgement: platform orders map to ib_async
  `MarketOrder`/`LimitOrder`/`StopOrder`/`StopLimitOrder` on `Stock`/`Index`
  contracts; `placeOrder` returns a live `Trade`; acknowledgement arrives
  through `orderStatusEvent` (PendingSubmit -> Submitted).
- Status updates: IB statuses map to platform statuses (PendingSubmit ->
  RECEIVED, PreSubmitted/Submitted/ApiPending/ApiUpdate -> SUBMITTED, Filled ->
  FILLED, Cancelled/ApiCancelled -> CANCELLED, Inactive/ValidationError ->
  REJECTED); PendingCancel is transitional and leaves the current status.
- Cancellation: `cancel_order` initiates `cancelOrder` on the live trade and
  records the request; the status change arrives through the callback.
- Replacement: re-submitting under the same order id modifies the existing IBKR
  order in place (same order id, no duplicate submission).
- Partial fills and commissions: `execDetailsEvent` records fills into the
  adapter-owned ledger (cumulative quantity, average price);
  `commissionReportEvent` accumulates commissions per order.
- Rejections: orderStatus Inactive/ValidationError and IBKR error code 201
  mapped to a live order mark it REJECTED; other errors are recorded via
  `get_last_error()` without changing order status.
- Bracket orders and OCA: `execute_bracket_order` builds the bracket via
  `ib.bracketOrder` (parent entry, take-profit and stop-loss protective legs,
  parent/child transmit chaining) and applies an OCA group to the protective
  legs; parent and child entries are tracked in the ledger.
- Retrieval (independent state, contract C2): `get_account_snapshot()`,
  `list_positions()` come from account summary/position values received from
  IBKR (event callbacks or `accountSummary`/`positions`); `list_open_orders()`
  from `openTrades()` when connected or the adapter-owned ledger otherwise;
  `list_completed_orders()` from `reqCompletedOrders(apiOnly=True)` when
  connected or the adapter-owned ledger otherwise. The adapter ledger is never
  seeded from OMS state; `sync_state` only pushes broker-side status into the
  OMS and flags ledger-only orders.

## External Setup Required

An operator must provide an IBKR paper account, approved TWS or Gateway paper
connection, network access, and a test window. The account and port must be
verified as paper before any connection test.

External tests (`trading-platform/tests/external/test_ibkr_paper_smoke.py`) are
marked `@pytest.mark.external` and skipped by default. They require
`IBKR_PAPER_SMOKE=1` plus `IBKR_PAPER_HOST`, `IBKR_PAPER_PORT`, and
`IBKR_PAPER_CLIENT_ID`; the connect-only test performs no order submission, and
the submission test additionally requires `IBKR_PAPER_ALLOW_SUBMISSION=1`. The
harness rejects live ports and accounts before connecting.

## Prohibitions

Never use live credentials, live ports, or a live account. Never infer paper
validation from fake-adapter tests. No IBKR connection or order submission has
occurred as part of this repository verification.
