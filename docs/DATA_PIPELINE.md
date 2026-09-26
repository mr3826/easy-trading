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

## Dataset PIT preflight gate (data-quality milestone, 2026-09-26)

`research/data_quality.py` + `scripts/run_data_preflight.py` — **IMPLEMENTED, TESTED**.
Run BEFORE any research; it refuses datasets that cannot support unbiased conclusions:

- bar integrity: UTC, calendar-aligned, OHLC sanity, NaN/finite, `available_at >= timestamp`
- a point-in-time **constituent membership manifest is required** (schema:
  `{"schema_version":"1.0","entries":[{"symbol","start","end"|null}]}`); without it the
  verdict is FAIL — research on today's survivors is rejected by gate, not by memory
- survivorship red flags: zero membership exits, static membership, sampled adds-only growth
- delisted members without price history: recorded as WARNING (untradeable-exits limitation)

Exit codes: 0 PASS · 1 PASS_WITH_WARNINGS · 2 FAIL · 3 REQUIRES_EXTERNAL_SETUP.

```
uv run python scripts/run_data_preflight.py \
    --data-dir <daily-bars> --benchmark SPY \
    --membership <universe_membership.json> \
    --output artifacts/research/data_preflight.json
```

Only after PASS (or consciously-accepted warnings) does `run_strategy_research.py` become
meaningful; feed the same manifest as `universe_membership` there to lift the
survivorship evidence ceiling.

What to procure, in what format, and the traps (current-constituents-only exports,
missing delisted prices): see [DATA_SOURCING.md](DATA_SOURCING.md). Membership manifests
are built from vendor CSVs by `scripts/build_membership_manifest.py`, which validates
through the same loader the preflight uses and warns on `exits=0` (survivor-only export).
