# MVP-1 — The Reproducible Research Application

## What MVP-1 is

A reproducible, operator-usable application that accepts legitimate
point-in-time (PIT) US-equity data, **validates the dataset before anything
else**, runs the registered strategy families, produces deterministic research
evidence, applies the promotion gate, and clearly reports whether anything
deserves shadow evaluation — **including the answer NO STRATEGY PROMOTED**.

MVP-1 succeeds when an operator supplies real data and receives a trustworthy
answer. "Nothing passed" is a valid, expected MVP outcome, not a failure.

## What MVP-1 is NOT

- Not live trading (`LIVE_TRADING_ENABLED=false`, `LIVE_STATUS=NOT_AUTHORIZED`
  are permanent repository policy; nothing in this app can change them).
- Not a web dashboard, mobile app, multi-tenant service, or streaming system.
- Not a strategy proof: passing the bundled tests proves the *workflow*, not
  alpha. Strategy evidence requires licensed PIT data and elapsed time.
- Not a 60-day paper pass: forward evidence is external and cannot be faked.

## Prerequisites

| Requirement | Notes |
|---|---|
| Python ≥ 3.12 | enforced; `doctor` checks it |
| [uv](https://docs.astral.sh/uv/) | locked dependencies (`uv.lock`) |
| ~2 GB RAM for research runs | pandas/numpy over daily bars |
| PostgreSQL 16 (optional for MVP research) | required only for OMS/shadow/paper persistence paths |
| Licensed PIT market data | **the external blocker** — see [DATA_SOURCING.md](DATA_SOURCING.md) |

## Installation (clean machine)

```bash
git clone https://github.com/mr3826/easy-trading && cd easy-trading
uv sync --all-extras --locked

uv run trading-platform doctor        # expect: BLOCKED → MVP_STATUS=REQUIRES_EXTERNAL_DATA
uv run pytest -m "not external"       # full local suite (postgres tests self-skip)
```

## Configuration

No Python source edits are ever required. See `.env.example`; `doctor`
validates the effective configuration and fails closed on unsafe values.

| Variable | Meaning |
|---|---|
| `TRADING_DATA_DIR` | directory of daily bar Parquets (`<SYMBOL>.parquet`) |
| `TRADING_MEMBERSHIP` | PIT membership manifest JSON (built, never hand-edited) |
| `TRADING_BENCHMARK` | benchmark symbol (default `SPY`) |
| `TRADING_RESEARCH_OUTPUT` | run-artifact root (default `artifacts/research`) |
| `TRADING_PROMOTIONS_ROOT` / `TRADING_EXPERIMENTS_ROOT` / `TRADING_JOURNAL_PATH` | durable roots |
| `DATABASE_URL` | optional isolated Postgres; checked by `doctor` when set |
| `LIVE_TRADING_ENABLED` / `LIVE_STATUS` | fixed safety values; unsafe inputs are rejected by policy |

## The canonical workflow

```bash
# 1) am I ready?
uv run trading-platform doctor

# 2) vendor membership CSV (symbol,start[,end]) -> validated PIT manifest
uv run trading-platform data build-membership \
    --csv vendor_membership_history.csv \
    --output artifacts/research/universe_membership.json \
    --source "vendor X membership export 2026-09"
#    -> if it prints exits=0, the export is current-constituents-only: REJECT it

# 3) gate the dataset (never research past FAIL; warnings need explicit ack)
uv run trading-platform data preflight \
    --data-dir /path/to/bars --benchmark SPY \
    --membership artifacts/research/universe_membership.json \
    --output artifacts/research/data_preflight.json

# 4) run every registered family through the full evidence pipeline
uv run trading-platform research run-all \
    --data-dir /path/to/bars --benchmark SPY \
    --membership artifacts/research/universe_membership.json \
    --output-dir artifacts/research \
    --accept-data-warnings        # only if preflight said PASS_WITH_WARNINGS

# 5) read the answer
uv run trading-platform status
uv run trading-platform report show            # MVP_RESEARCH_SUMMARY.md
uv run trading-platform research latest        # JSON
uv run trading-platform research list-strategies
uv run trading-platform research run --family trend_relative_strength ...   # one family
```

Legacy scripts (`scripts/build_membership_manifest.py`,
`scripts/run_data_preflight.py`, `scripts/run_strategy_research.py`) remain as
thin backward-compatible wrappers over the same services.

### What run-all guarantees

1. Runs the PIT data preflight exactly once.
2. **Refuses FAIL** — research never starts on untrustworthy data.
3. **Refuses PASS_WITH_WARNINGS without `--accept-data-warnings`**; when
   accepted, the warning list + acknowledgement are persisted into the dataset
   manifest, every family report's metadata/known-biases, and the MVP summary.
4. Pins a **dataset fingerprint** (SHA-256 over bar file checksums, membership
   manifest + provenance, benchmark, date range, data-quality version, verdict,
   accepted warnings) plus the **git commit**, **parameter grids**, **cost
   model**, **bootstrap seed**, **walk-forward settings** and **promotion
   policy hash** into every artifact.
5. Runs all registered researchable families
   (`trend_relative_strength`, `breakout_volume`, `trend_pullback`);
   `ma_cross_baseline` stays a registered control.
6. Retains every attempted configuration (failed trials are data; a crashed
   family still records its attempted grids as `NOT_ATTEMPTED` and yields
   `RESEARCH_INCOMPLETE`, never a silent rejection).
7. Writes per-family JSON+Markdown reports, promotion decision artifacts,
   `experiments.json`, and `MVP_RESEARCH_SUMMARY.{json,md}`.
8. Makes no recommendation the promotion gate did not support.

### Expected output shape (a *successful* MVP run)

```text
DATA QUALITY: PASS
trend_relative_strength      REJECTED
breakout_volume              REJECTED
trend_pullback               REJECTED
FINAL_VERDICT: NO_STRATEGY_PROMOTED
run_id=20260926T120000Z-1a2b3c4d dataset_fingerprint=1a2b3c4d...
summary=artifacts/research/<run-id>/MVP_RESEARCH_SUMMARY.json
next: NO STRATEGY PROMOTED. ...
```

## Artifacts

```text
artifacts/research/<run-id>/
    data_preflight.json
    dataset_manifest.json          # immutable fingerprint + provenance
    experiments.json               # every config ever tried in this run
    family-*.json / family-*.md    # per-family full evidence reports
    promotion/                     # immutable promotion decision artifacts
    MVP_RESEARCH_SUMMARY.json
    MVP_RESEARCH_SUMMARY.md        # the human answer
```

Artifacts are ignored by git except `artifacts/verification/` (CI evidence).

## Product state model (`trading-platform status`)

```text
NOT_CONFIGURED                 → set TRADING_DATA_DIR / TRADING_MEMBERSHIP
REQUIRES_EXTERNAL_DATA         → paths set but files missing (get licensed data)
DATA_FAILED                    → preflight FAIL (fix the dataset; the refusal is
                                 persisted as a run record so this state survives)
DATA_READY                     → gate inputs present, no run yet
RESEARCH_INCOMPLETE            → a family crashed; the gate never judged part
                                 of the trial space (fix and re-run)
NO_STRATEGY_PROMOTED           → run completed; policy rejected every family
STRATEGY_RESEARCH_ONLY         → evidence insufficient to judge
STRATEGY_APPROVED_FOR_SHADOW   → a config passed every promotion evidence class
NOT_AUTHORIZED_LIVE
```

Shadow/paper/live boundaries are always printed by `status` as fixed policy
text — they are not states this application transitions through.

## Exit codes (documented contract)

| Command | Code | Meaning |
|---|---|---|
| any | `0` | success (`NO_STRATEGY_PROMOTED` is success) |
| doctor | `1` | hard failure (unwritable paths, unreachable configured DB, invalid manifest) |
| doctor | `3` | MVP operation currently blocked (data not configured/found) |
| data preflight | `0/1/2/3` | PASS / PASS_WITH_WARNINGS / FAIL / REQUIRES_EXTERNAL_SETUP |
| research run, run-all | `1` | usage or unexpected error, or a crashed family (`RESEARCH_INCOMPLETE`) |
| research run-all | `2` | data FAIL / invalid manifest / refused non-PIT request |
| research run-all | `3` | REQUIRES_EXTERNAL_DATA (files missing) |
| research run-all | `4` | PASS_WITH_WARNINGS without `--accept-data-warnings` |

## Failure states are first-class

- Missing membership manifest → blocked (exit 3 with next-step text).
- A preflight FAIL is itself persisted as a `DATA_FAILED` run record, so
  `status` reflects the current dataset instead of a stale passing run.
- Zero membership exits / static membership / invalid ranges → preflight FAIL.
- OHLC violations, NaN/non-finite, off-calendar bars, timestamp violations,
  incompatible benchmark calendar → preflight FAIL, research refuses.
- Insufficient OOS evidence (PBO/folds/trade count) → REJECTED or
  RESEARCH_ONLY — **never** APPROVED. The gate fails closed on missing
  evidence classes.
- Exploratory runs without a manifest require an explicit
  `--allow-non-pit` flag and are permanently labelled
  `evidence_ceiling=CAPPED`.

## Safety boundary

- The CLI imports no broker/OMS/risk/simulator module (regression-tested).
- Promotion artifacts are immutable and policy-hash bound; approvals under an
  old policy are ignored by the paper orchestrator.
- Shadow/paper/live remain gated behind promotion, reconciliation, and
  permanent authorization policy. Productization added zero execution paths.
- `doctor`/`status` never print environment secrets.

## External blockers (not fixable in code)

1. Licensed daily OHLCV bars + benchmark history.
2. Historical constituent membership **including removals** and delisted-symbol
   price coverage (the binding constraint).
3. 60+ trading days of forward shadow/paper evidence (elapsed time).
4. IBKR paper gateway connectivity/credentials.
5. Live authorization — permanently out of scope.

## Definition of done (MVP-1 code)

- One canonical CLI (`status`, `doctor`, `data`, `research`, `report`) reusing
  existing services — no duplicated engines.
- Gate-Zero enforcement + explicit warning acknowledgement, persisted.
- Dataset fingerprint + git commit + policy hash on every artifact.
- `run-all` produces the cross-family MVP summary; NO_STRATEGY_PROMOTED is a
  success.
- Clean-machine install (`uv sync --all-extras --locked`) + `doctor` + full
  suite green; ruff/format/mypy/pip-audit/secret-scan/coverage gates hold.
- E2E test proves the whole operator path, positive and negative.
- This document matches the shipped behavior.

Operational research on real data remains gated on the external blockers
above: `MVP1_CODE_READY` can be true while
`MVP1_OPERATIONAL_RESEARCH=REQUIRES_EXTERNAL_DATA`.
