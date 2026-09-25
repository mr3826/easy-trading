# ADR-002: Cash Account, No Leverage

## Status
Accepted — implemented and CI-verified (2026-09-25)

## Context
The account model must be specified for V1. Cash-only eliminates margin calls, liquidation risk, and complex borrowing costs.

## Decision
V1 shall use a cash account model with no leverage. All positions must be fully paid for with settled cash.

## Consequences
- No margin calls or forced liquidations
- No interest calculations or borrowing costs
- Position size limited by available settled cash
- Simpler risk engine (no margin checks)
- Broker compatibility: cash mode is widely supported by IBKR paper trading
- Strategies must size positions based on current cash balance, not margin availability

## Related ADRs
- ADR-001: Daily bars as only timeframe
- ADR-005: Common core architecture