# Data Pipeline

Daily completed US equity bars (ADR-001, ADR-004).

```
vendor pull -> raw archive (immutable) -> validation -> Parquet store (versioned,
supersession-aware) -> point-in-time retrieval -> feature/regime computation
```

- Ingestion: `data/ingestion/daily_bar_ingestion.py` (checksums, raw archive, universe split
  engineering vs research). **IMPLEMENTED, TESTED.**
- Store: `ParquetMarketDataProvider` with metadata + deterministic retrieval timestamps.
- Persistence: PostgreSQL for state/experiments (migrations `0001`, `0002`), Parquet for bars.
- Vendor choice: pending (see `data-vendor-decision-matrix.md`) — **REQUIRES EXTERNAL SETUP**.

The pipeline is deliberately boring: no streaming infra, no message bus. Daily bars only.
