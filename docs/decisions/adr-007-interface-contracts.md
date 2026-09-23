# ADR-007: Cross-Worker Interface Contracts

## Status

Accepted (Wave 0, orchestrator)

## Context

Six specialist workers implement the remaining platform in parallel with
non-overlapping file ownership. Several capabilities span workers: market data
is produced by one worker and consumed by two; independent broker state flows
from the adapter into reconciliation; shadow decisions persist through
PostgreSQL repositories owned by another worker. Without pre-agreed contracts,
parallel edits would diverge and integration would fail.

## Decision

1. All cross-worker interfaces are declared in `docs/plans/INTERFACE_CONTRACTS.md`
   before parallel implementation begins (contracts C1-C6).
2. Each contract names exactly one provider (owner of the producing files) and
   its consumers; consumers never edit provider files.
3. Independent state sources are mandatory: broker snapshots are built only
   from independently received broker-side data; reconciliation compares
   internal state derived from durable ledger events against that independent
   snapshot.
4. Optional external dependencies (IBKR client library) are pinned centrally in
   `pyproject.toml` by the orchestrator; workers record needs under
   `DEPENDENCY_REQUESTS` in their handoff.
5. Deterministic fakes ship with every external-facing interface so default
   tests remain offline.

## Consequences

- Parallel work can proceed without file conflicts or blocked workers.
- Integration verifies that broker, shadow, OMS, and reconciliation contracts
  connect correctly; duplicated implementations are resolved, not kept.
- Any post-launch contract change is an explicit, documented event.

## Related ADRs

- ADR-004: PostgreSQL + Parquet persistence
- ADR-005: Common core architecture
- ADR-006: Execution adapter pattern
- ADR-008: IBKR client library (Agent E)
