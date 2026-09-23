# Shadow Operator Guide

## Purpose

Shadow mode runs the real completed-bar strategy and risk logic while writing
`WOULD_SUBMIT` decisions. It is designed to compare decisions with archived
replays without creating broker orders.

## Implementation

- `trading_platform/shadow.py`: `ShadowOrchestrator` consumes completed bars
  from an injected `MarketDataProvider`-like provider (`get_bars(instrument,
  start, end)`), validates every bar, detects stale, missing, revised,
  duplicate, and invalid data, runs the injected strategy callable and the
  injected hard-risk engine (`check_order(...)`), and records decisions only
  when the strategy submits and hard risk approves.
- `trading_platform/persistence/shadow_archive.py`: `ShadowArchive` is a
  deterministic, checksummed JSONL archive of raw inputs, decisions, problems,
  and discrepancies. It satisfies the `ShadowDecisionSink` protocol (contract
  C3) and forwards decisions to an optional downstream database sink; the
  file-based archive remains fully functional when the database sink is not
  configured.
- Replay: `ShadowOrchestrator.replay()` re-runs the decision pipeline over the
  archived inputs without writing; `verify_replay()` compares the replayed
  decisions against the archived records exactly, persists any mismatch as a
  discrepancy, and escalates by raising `ShadowReplayMismatch`.

## Operating Steps

1. Set `TRADING_ENV=shadow` and keep live variables at their permanent safe values.
2. Configure a completed-bar provider, a hard-risk engine, a strategy callable,
   and a `ShadowArchive` archive destination.
3. Call `orchestrator.run(instrument, start, end)` per window: every received
   bar is archived, problems are persisted to `problems.jsonl`, and
   problematic bars are skipped so they cannot produce new decisions.
4. `WOULD_SUBMIT` decisions are written to `decisions.jsonl` and forwarded to
   any configured `ShadowDecisionSink` database sinks.
5. After each window call `orchestrator.verify_replay()`: a mismatch is
   persisted to `discrepancies.jsonl` and raised for escalation.
6. Treat a raised `ShadowReplayMismatch` as an incident: stop, investigate the
   discrepancy records, and do not resume until resolved.

## Structural Submission Impossibility

The `ShadowOrchestrator` exposes no order-submission method
(`execute_order`/`submit`/`place_order` do not exist on the shadow path), and
`ShadowBrokerAdapter` remains sink-only with no `execute_order` method. Tests
assert both. Even when a broker-shaped object is registered as a decision
sink, the orchestrator only ever calls `record_shadow_decision` on it.

The legacy `run_shadow` helper is retained only for backward compatibility; it
performs no validation or archival and must not be used for shadow operation.

## Safety Checks

Missing, stale, duplicate, revised, or invalid bars block new decisions for
the affected slot: the problem is persisted, the bar is skipped, and the run
is recorded as blocked. Data-quality problems are detected against the
injected provider and clock, both of which are chaos-injectable seams.
Shadow operation does not prove paper performance or strategy profitability.

## Evidence Ceiling

The code and isolation tests are CI-verifiable. No real shadow forward run or
shadow performance claim is recorded in this repository.
