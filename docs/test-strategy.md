# Test Strategy

## Test Layers

- Unit tests prove domain validation, deterministic serialization, risk rules,
  OMS transitions, AI rejection, and adapter isolation.
- Property-oriented tests exercise idempotency, state transitions, and
  deterministic behavior with varied inputs.
- Integration tests exercise PostgreSQL migrations, transactions,
  append-only journal behavior, repository constraints, and recovery.
- External tests are marked and skipped by default; none submit orders here.
- CI tests run on pull requests and protected-branch pushes.

## Required Safety Assertions

Tests must assert the fail-closed state after dependency faults, stale data,
reconciliation mismatch, authorization expiry, broker rejection, partial
fills, restart recovery, and malformed AI output. A passing count alone is not
evidence of correctness.

## Discovery and Inventory

Pytest uses `trading-platform/tests` explicitly. The baseline 47-node inventory
and final inventory are stored under `artifacts/verification/`. Five duplicate
`test_wf2.py` nodes were intentionally removed after consolidation; their
canonical equivalents remain.

## Coverage

CI enforces at least 80% overall production coverage and 90% aggregate coverage
for OMS, risk, reconciliation, and authorization. Coverage supplements
behavioral assertions; it does not replace them.
