# ADR-005: Common Core Architecture

## Status
Proposed

## Context
The platform must run the same strategy, risk, OMS, event, and portfolio code in every environment (simulation, shadow, paper, live). Only data and execution adapters change.

## Decision
One common core Python process implements all strategy, risk, OMS, and portfolio logic. Environment-specific behavior is confined to data providers and execution adapters.

## Consequences
- Single codebase across all environments reduces bugs from divergence
- Environment adapters (data, broker) are the only source of variation
- Testing focus: verify adapters, not core logic, change per environment
- Deterministic replay requires fixed data + fixed core code version
- Fails closed: any environment mismatch blocks new orders

## Related ADRs
- ADR-001: Daily bars as only timeframe
- ADR-002: Cash account, no leverage
- ADR-004: PostgreSQL + Parquet persistence
- ADR-006: Execution adapter pattern