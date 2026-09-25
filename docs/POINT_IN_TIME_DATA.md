# Point-in-Time Data

Stores: `data/` (Parquet provider), `data/ingestion/` — **IMPLEMENTED, TESTED, CI-VERIFIED**.

## Properties

- Bars carry `timestamp` (event time) and `available_at` (when usable). Retrieval is
  point-in-time: a bar is returned only if `available_at <= as_of`. Superseded corrections
  are retained and queryable via `get_superseded`, never silently rewritten into history.
- `validate_bar` / `validate_data_integrity` reject malformed bars, non-UTC timestamps,
  negative prices/volumes, and broken OHLC relationships; the orchestration layer must not
  proceed on failed validation.
- Features and regimes consume only point-in-time frames (see
  [FEATURE_CATALOG.md](FEATURE_CATALOG.md) and the prefix-stability tests).
- Corporate actions (splits/dividends) are versioned adjustments
  (`apply_split_adjustment`, `apply_dividend_adjustment`).

## Known ceiling

The research universe is an **engineering universe**, not point-in-time index constituents.
Until constituent membership is point-in-time, all backtests carry a survivorship-bias
ceiling and are labeled `CAPPED` in reports. See [LIMITATIONS.md](LIMITATIONS.md).
