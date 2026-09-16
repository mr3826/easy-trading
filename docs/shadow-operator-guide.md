# Shadow Operator Guide

## Purpose

Shadow mode runs the real completed-bar strategy and risk logic while writing
`WOULD_SUBMIT` decisions. It is designed to compare decisions with archived
replays without creating broker orders.

## Operating Steps

1. Set `TRADING_ENV=shadow` and keep live variables at their permanent safe values.
2. Configure a completed-bar provider and an archive destination.
3. Validate and archive raw inputs before strategy evaluation.
4. Run strategy and hard-risk logic through `ShadowBrokerAdapter`.
5. Persist decisions, data freshness problems, revisions, and discrepancies.
6. Replay the archive and compare deterministic decision records.

The `ShadowBrokerAdapter` has no `execute_order` method. The legacy session
operator also records no hypothetical fills and must not be given a real
broker adapter.

## Safety Checks

Missing, stale, duplicate, revised, or provenance-unknown bars block new
decisions. A replay mismatch is persisted as a discrepancy and escalated.
Shadow operation does not prove paper performance or strategy profitability.

## Evidence Ceiling

The code and isolation tests are CI-verifiable. No real shadow forward run or
shadow performance claim is recorded in this repository.
