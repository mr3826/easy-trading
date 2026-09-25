# ADR-001: Daily Bars as Only Timeframe

## Status
Accepted — implemented and CI-verified (2026-09-25)

## Context
The trading platform must select a single bar timeframe for V1. The execution plan explicitly states daily bars as the V1 timeframe.

## Decision
V1 shall use daily bars only. No intraday or high-frequency data will be supported in V1.

## Consequences
- Strategy logic operates on end-of-day data only
- Order signals are generated once per day
- No intra-day market scanning or monitoring
- Simplifies data ingestion, storage, and reconciliation
- Aligns with cash account model (no intraday buying power calculations)

## Related ADRs
- ADR-002: Cash/no leverage account model
- ADR-005: Common core architecture