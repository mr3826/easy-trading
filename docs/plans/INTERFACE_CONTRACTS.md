# Interface Contracts — Wave 0

Authoritative contracts shared by multiple workers. These are resolved BEFORE
parallel implementation begins. Changes after launch require orchestrator
approval and a note in this file.

## C1 — Market data (Agent A provides; B and D consume)

```python
class MarketDataProvider(Protocol):
    def get_bars(self, instrument: Instrument, start: datetime, end: datetime,
                 session: TradingSession = TradingSession.DAY) -> List[Bar]: ...
    def has_bars(self, instrument: Instrument, start: datetime, end: datetime) -> bool: ...
    def get_latest_bar(self, instrument: Instrument) -> Bar | None: ...
    def get_metadata(self, instrument: Instrument) -> DataMetadata: ...
```

Contract terms:
- `get_bars()` returns real, validated records read from Parquet (existing
  signatures in `trading_platform/data/__init__.py` are kept).
- Point-in-time: only bars with `timestamp <= end` and available at the
  as-of time are returned; future rows can never leak.
- `DataMetadata.checksum` is a real SHA-256 of file contents;
  `retrieval_timestamp` is deterministic for a fixed file.
- Duplicate timestamps are rejected; missing/stale/revised bars are detected
  and reported, never silently dropped.
- Raw vendor inputs are preserved separately from normalized data.
- Deterministic `FakeMarketDataProvider` ships for tests; no real vendor is
  required or contacted.

## C2 — Independent broker state (Agent E feeds; Agent C consumes)

```python
@dataclass(frozen=True)
class BrokerSnapshot:          # exists in domain/__init__.py, currently unused
    positions: Dict[str, int]
    cash: float
    buying_power: float | None = None
```

Contract terms:
- A broker snapshot must be built ONLY from data independently received from
  the broker side (adapter-internal state, IBKR callbacks, or a deterministic
  fake-broker ledger), never from OMS-derived copies.
- `IBKRPaperBrokerAdapter` exposes `get_account_snapshot()` /
  `list_positions()` / `list_open_orders()` / `list_completed_orders()`
  sourced from IBKR callbacks or the local fake ledger.
- Reconciliation compares an internal snapshot (derived from durable ledger
  events) against an independently obtained broker snapshot. Tests must not
  compare duplicated copies of the same state and call that reconciliation.
- On mismatch: `blocks_new_orders=True` + incident persisted + critical alert
  emitted + `OPERATOR_RESOLUTION_REQUIRED` recorded.

## C3 — Shadow decision sink (Agent D consumes; Agent C provides the DB writer)

```python
class ShadowDecisionSink(Protocol):
    def record_shadow_decision(self, decision: Mapping[str, Any]) -> None: ...
```

Contract terms:
- C adds a `shadow_decisions` table in `migrations/` and
  `PostgresStore.record_shadow_decision()` (C owns both files).
- D's shadow orchestrator takes a `ShadowDecisionSink` (Postgres-backed or
  file-based archive) and never edits C's files.
- Shadow mode: consumes only completed bars, archives all received inputs,
  runs production-equivalent strategy and hard-risk decisions, persists
  `WOULD_SUBMIT` decisions, and makes broker submission structurally
  impossible (no order-submission method exists on the shadow path).
- Replay: archived inputs are replayed deterministically and compared;
  mismatches are persisted as discrepancies.

## C4 — Risk gate (Agent C provides; D and the simulator consume)

- `HardRiskEngine.check_order(...)` must consult the reconciliation state:
  unresolved mismatch or `blocks_new_orders=True` ⇒ order rejected.
- Generated orders cannot breach cash, exposure, position-count (max 3),
  daily-loss, or drawdown limits; all limits have property-based tests.
- Commission and slippage values come from configuration/fill records, never
  hard-coded placeholders.

## C5 — IBKR paper boundary (Agent E)

- Client library pinned centrally (`ib_async`, documented in ADR-008 with
  alternatives); `broker_adapter.py` must not import it at module level.
- Two-flag gate: one flag allows connection, a second flag permits paper-order
  submission; known live ports (7496/4001 live, 7497/4002 paper per IBKR
  convention) and live account configuration are rejected.
- Default tests are deterministic and offline; external tests are
  `@pytest.mark.external` and skipped unless explicitly enabled.
- No credentials, no external connections, no orders.

## C6 — ML/LLM boundary (Agent F)

- Chronological train/validation/test separation; timestamp provenance per
  feature and label.
- LLM features: strict schema (`symbol`, `sentiment`, `confidence`,
  `observed_at`), rejection of malformed JSON, NaN/infinity, unknown symbols,
  stale content, contradictory output, prompt injection, timeouts, and
  upstream failures ⇒ `FEATURE_REJECTED`.
- AI failure never means "trade anyway"; the boundary imports no broker
  modules, touches no credentials, exposes no order methods.
- Promotion criteria defined before evaluation; no single improved metric
  promotes a model; historical LLM results do not authorize promotion.
