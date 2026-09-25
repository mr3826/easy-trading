# ADR-006: Execution Adapter Pattern

## Status
Accepted — implemented and CI-verified (2026-09-25)

## Context
The platform must interface with multiple execution environments (simulator, shadow, IBKR paper, live) without leaking broker-specific types into the domain layer.

## Decision
- Define a `BrokerAdapter` contract that abstracts all broker interaction.
- IBKR API sits behind the `BrokerAdapter`; no IBKR-specific types may leak into the domain layer.
- Adapters implement: connection lifecycle, order transmission, parent/child legs, OCA behavior, rejection mapping, cancel/replace, fills, account snapshots.
- A deterministic fake broker is used for contract tests before any real broker integration.
- Execution adapters are the only layer that knows about broker-specific order types, IDs, and protocols.

## Consequences
- Core domain logic remains broker-agnostic and testable.
- Switching brokers or adapters requires only the adapter layer to change.
- Broker-specific edge cases and quirks are contained in adapter implementations.
- Contract tests with a fake broker give confidence before connecting to a real broker.

## Related ADRs
- ADR-005: Common core architecture
- ADR-004: PostgreSQL + Parquet persistence