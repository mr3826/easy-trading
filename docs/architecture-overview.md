# Architecture Overview

## Boundaries

The platform is a modular Python application. Daily-bar ingestion produces
validated point-in-time inputs. Strategies produce deterministic signals. The
simulator consumes completed bars and only executes on a later eligible event.
The risk engine evaluates hard limits before OMS submission. The OMS owns
order lifecycle and idempotency. PostgreSQL stores the journal and durable
execution state. Parquet stores research data and raw archives.

`FakeBrokerAdapter` is an in-process test adapter. `ShadowBrokerAdapter` is a
sink that records `WOULD_SUBMIT` decisions and has no submission method.
`IBKRPaperBrokerAdapter` is a paper-only external boundary and currently
refuses to connect until external setup is supplied.

## Trust Flow

```text
completed bar -> validation/archive -> strategy -> signal
  -> hard risk -> OMS intent -> durable journal/PostgreSQL
  -> fake/shadow/paper boundary
```

There is no path from the LLM feature parser to an adapter. Unknown or
malformed AI output is rejected as `FEATURE_REJECTED`.

## Persistence

`trading-platform/migrations/0001_core.sql` defines journal, signal, risk,
order, fill, position, snapshot, reconciliation, incident, policy, model, and
authorization tables. Journal rows are append-only through a database trigger.
Order intents are idempotent, broker order IDs are unique, and order
transitions require a persisted risk decision in the same durable boundary.

## Failure Policy

Database, broker, data, clock, reconciliation, and authorization uncertainty
blocks new orders. Reconciliation mismatches persist an incident and require
verified resolution. The standalone dead-man check is independent of the
trading process.

## Non-goals

Kafka, Kubernetes, Redis, microservices, and production deployment are not V1
requirements. No infrastructure is implied by this document beyond the
modular application, PostgreSQL, Parquet, CI, and configured alert transports.
