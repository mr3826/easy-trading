# Risk Management

Hard authority: `risk/risk_engine.py` (`HardRiskEngine`) + `risk/limits.py` —
**IMPLEMENTED, TESTED, CI-VERIFIED (>=90% critical coverage enforced in CI)**.
Kill switches: `risk/kill_switch.py` — see [KILL_SWITCHES.md](KILL_SWITCHES.md).
Policy values: see `risk-policy.md`.

## Non-negotiables

- Strategy/ML/LLM code can never override, bypass, or relax the risk engine. The risk
  engine is the final authority on every order (tested in `test_safety_boundaries.py`).
- Long-only, cash account, no leverage (ADR-002). V1 max 3 positions.
- Risk policies are versioned (`RiskPolicyVersion`); the orchestrator pins the policy version.
- Kill-switch BLOCK affects **new exposure only**. Cancel-all and liquidation are distinct,
  explicit actions; a block never implies liquidation.
- Unknown state rejects/fails closed everywhere (risk checks, reconciliation, orchestration).

## Controls

Per-order: price sanity, quantity, notional, position limits, sector concentration,
buying power/settled cash. Portfolio: drawdown, turnover, gross exposure, cash reserve.
Session: startup/session-end reconciliation gates (`SessionScheduler.should_trade`).
Strategy-level: disable strategy/symbol, block new positions, disable all submissions.
