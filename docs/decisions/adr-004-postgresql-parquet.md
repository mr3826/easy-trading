# ADR-004: PostgreSQL + Parquet Persistence

## Status
Accepted — implemented and CI-verified (2026-09-25)

## Context
The platform needs a persistence layer for orders, fills, positions, risk decisions, journal, configuration, and model metadata. Market and research data must be versioned and immutable.

## Decision
- **Orders, fills, positions, risk decisions, journal, configuration, model metadata**: stored in PostgreSQL with migrations.
- **Market and research data**: stored in versioned Parquet files with dataset manifests.
- PostgreSQL is the local source of truth; actual broker positions, orders, fills, and cash override local assumptions after reconciliation.
- Parquet provides immutable/versioned market and research data with checksums, schema version, and dataset version.

## Consequences
- Relational database guarantees ACID for financial state
- Parquet enables efficient columnar reads for backtest/replay workloads
- Source timestamps, retrieval timestamps, vendor identifiers, licenses, checksums, schema version, and dataset version are preserved
- Two-universe concept: engineering universe (small fixed list for plumbing tests) vs research universe (point-in-time membership including removed/delisted names)

## Related ADRs
- ADR-005: Common core architecture
- ADR-006: Execution adapter pattern