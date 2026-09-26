# Data Sourcing Specification (External Milestone)

This is the procurement checklist for the next milestone. Nothing strategy-related
matters until a dataset passes `trading-platform data preflight` (see
[DATA_PIPELINE.md](DATA_PIPELINE.md)). The repository can validate and refuse data;
it cannot obtain it. **The binding constraint is historical constituent membership
with removals, and delisted-symbol price coverage.**

## Required inputs

| # | Input | Requirement | Preflight consequence if absent |
|---|---|---|---|
| 1 | Daily OHLCV bars | UTC timestamps, split/dividend-adjusted AND raw where possible, one Parquet per symbol named `<SYMBOL>.parquet` | CRITICAL: bar-integrity / calendar checks |
| 2 | Benchmark series (e.g. SPY) | Same trading calendar as universe | CRITICAL: calendar/benchmark checks |
| 3 | **Historical constituent membership** | Every entry AND exit date per symbol per period, including symbols that were later removed. Build via `trading-platform data build-membership` from a vendor CSV (`symbol,start,end`) | CRITICAL FAIL without any manifest; FAIL on zero exits / static membership |
| 4 | **Delisted-symbol price history** | Price bars for symbols that left the universe (bankruptcies, mergers, delistings) | WARNING `member-no-bars` = exits untradeable ⇒ results still biased; treat as data defect to fix |
| 5 | Availability timestamps | `available_at` >= bar close per bar | WARNING if absent (availability assumed) |
| 6 | Corporate actions | Split/dividend events with announcement dates (ingestion path supports them) | Adjustments unverifiable without them |
| 7 | Revision policy | Corrections kept as superseded versions, never silently rewritten | Point-in-time store supports this; vendor feed must not force it |
| 8 | License | Research redistribution/storage permitted; document vendor + export date | Manifest `--source` provenance field |

## Where to source each input (operator research task)

- Bars + corporate actions: vendor daily US equity history (licensed feeds or
  exchange-derived products; `docs/data-vendor-decision-matrix.md` has the decision grid).
- **Membership with exits + delisted prices are usually SEPARATE products.** Index
  membership histories (e.g. licensed index constituent files) and survivorship-bias-free
  equity databases (delistings included) are commonly sold apart from bar feeds. A
  universe built by downloading "current S&P 500 constituents' histories" is exactly the
  contamination the preflight fails — do not start there.
- Acceptance test before trusting any vendor export: run the builder; if it reports
  `exits=0`, the export is current-constituents-only and must be rejected.

## Operating sequence

```bash
# 1) build manifest from the vendor membership CSV
uv run trading-platform data build-membership \
    --csv vendor_membership_history.csv \
    --output artifacts/research/universe_membership.json \
    --source "vendor X membership export YYYY-MM"

# 2) gate the dataset (exit codes: 0 pass, 1 warnings, 2 fail, 3 external setup)
uv run trading-platform data preflight \
    --data-dir <daily-bars> --benchmark SPY \
    --membership artifacts/research/universe_membership.json \
    --output artifacts/research/data_preflight.json

# 3) run every registered family through the canonical MVP workflow
#    (FAIL refuses; PASS_WITH_WARNINGS needs --accept-data-warnings)
uv run trading-platform research run-all \
    --data-dir <daily-bars> --benchmark SPY \
    --membership artifacts/research/universe_membership.json \
    --output-dir artifacts/research \
    --accept-data-warnings

# 4) read the answer
uv run trading-platform report show
```

The `scripts/build_membership_manifest.py`, `trading-platform data preflight`
and `scripts/run_strategy_research.py` entry points remain as
backward-compatible wrappers over the same services.

Reading PASS_WITH_WARNINGS: every warning names a data defect and its research
consequence. Accepting them is a documented human judgment; they must be listed in the
research report's known-biases section, never absorbed silently.

## Verdict semantics (unchanged by source)

`PASS` — dataset can support unbiased research conclusions.
`PASS_WITH_WARNINGS` — conclusions carry named limitations; record them.
`FAIL` — do not run strategy research on this data at all.
