# Order Execution

Layers: `trade_planning.TradePlan` -> `HardRiskEngine.check_order` -> `oms.OMS` ->
`broker.BrokerAdapter` (paper) — **IMPLEMENTED, TESTED**.

## Guarantees

- **Deterministic decision IDs**: `sha256(strategy_id | decision_timestamp | plan_id)`;
  the append-only `DecisionJournal` makes replays after restart no-ops (tested).
- **Idempotency keys** in OMS prevent duplicate submissions; duplicate-uncertainty blocks
  new exposure via kill switch.
- Entries are `MARKET_NEXT_OPEN` (or LIMIT) with modeled commission + slippage;
  OCA groups support bracket orders at the broker.
- No submission from stale data, unhealthy reconciliation, post-kill-switch, unapproved
  strategy, or unvalidated feature set — each gate is enforced in
  `orchestration.PaperOrchestrator` and tested.
- Broker code never infers permission: paper submission requires the explicit paper flag
  and risk-approved orders (`IBKRPaperBrokerAdapter._require_submission_allowed`,
  `_require_risk_approval`).

## Paper orchestration cycle (`orchestration/`)

Fixed order, fail-closed at each step:
preflight kill-switch (incl. promotion + drift gates) -> eligibility filter -> ranking
(score cannot confer eligibility) -> TradePlan/sizing -> hard risk approval -> OMS/broker
submission (idempotency key) -> journal -> reconciliation health check before any exposure.
