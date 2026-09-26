# MVP-1 Final Report — Easy Trading

Date: 2026-09-26 · Executor: senior-engineering audit task · Repository: `mr3826/easy-trading`

## Identification

```text
STARTING HEAD  f854ae1104d80546e48747f1fe83aa77e310e7a1  (verified == claimed baseline)
FINAL HEAD     9094ad104895d7da986f97e5bc080ece944fd636  (code-final; this report commit is docs-only)
BRANCH         feature/mvp1-readiness
PULL REQUEST   #7 — https://github.com/mr3826/easy-trading/pull/7  (NOT merged — human decision)
```

### Commits (chronological)

```text
cf57dcc audit: establish mvp1 product readiness baseline
7fcf488 feat: unify operator workflows under platform cli
398a909 test: add end-to-end mvp acceptance coverage
1413e1a fix: correct cli surface defects found in review
76ac3b3 docs: publish mvp1 operator and readiness guides
7844622 test: make doctor-based tests independent of ambient DATABASE_URL
a7ed4bb fix: non-PIT evidence can never mint paper-eligible APPROVED artifacts
61ccc4b docs: publish mvp config contract in environment guide and record green PR CI
9094ad1 feat: report an explicit trade-activity proxy instead of silence on turnover
```

## Status

```text
MVP1 CODE STATUS          READY (all code-verifiable exit criteria pass — see matrix)
MVP1 PRODUCT STATUS       READY — one canonical operator CLI end-to-end (status/doctor/data/research/report)
DATA STATUS               REQUIRES_EXTERNAL_DATA (licensed PIT bars + membership-with-exits + delistings)
STRATEGY STATUS           NO STRATEGY PROMOTED (none claimed; policy refuses every configuration)
SHADOW STATUS             NOT STARTED — requires an APPROVED promotion artifact + forward evidence
PAPER STATUS              REQUIRES_EXTERNAL_SETUP (adapter complete/unit-tested; no gateway/credentials)
LIVE STATUS               NOT_AUTHORIZED (permanent policy; unchanged and unchangeable via this code)
```

## CLI commands added / changed

| Command | Purpose |
|---|---|
| `trading-platform status` | deterministic MVP state (`NOT_CONFIGURED → … → NO_STRATEGY_PROMOTED/…`) + preserved live flags |
| `trading-platform doctor` | 14 operability checks (python, package+git revision, config safety, DB+migrations, writable roots, dataset+manifest validation, live boundary, IBKR-paper state, promotion artifacts, journal, clock); non-zero when MVP operation impossible; no secret output |
| `trading-platform data build-membership` | vendor CSV → schema-validated PIT manifest + round-trip check + zero-exit warning |
| `trading-platform data preflight` | fail-closed gate; exit codes 0/1/2/3 identical to legacy script |
| `trading-platform research list-strategies` | registry incl. pinned parameter grids + control status |
| `trading-platform research run` | one family — gated by `--membership`; exploratory only via explicit `--allow-non-pit` (labelled CAPPED) |
| `trading-platform research run-all` | canonical MVP workflow: preflight → FAIL refusal → warning acknowledgement → fingerprint → all researchable families → per-family reports → promotion → cross-family summary |
| `trading-platform research status` / `latest` | persisted run verdicts / newest summary JSON |
| `trading-platform report show` | `MVP_RESEARCH_SUMMARY.md`/`.json`, `--family` full report |
| `trading-platform --status` | legacy two-line output preserved byte-identical |
| `scripts/build_membership_manifest.py`, `scripts/run_data_preflight.py`, `scripts/run_strategy_research.py` | converted to thin backward-compatible wrappers over shared services (no duplicated logic; exit-code contracts kept) |

## Verification results

### Fresh install (clean clone, Windows/Python 3.14 + CI clean Ubuntu/3.12.14)

```text
git clone → uv sync --all-extras --locked            exit 0 (lock-resolved, no network drift)
uv run trading-platform doctor                       exit 3, MVP_STATUS=NOT_CONFIGURED + BLOCKED data (by design)
uv run pytest trading-platform/tests/unit -q         green on the fresh environment
uv run trading-platform status / research …          smoke OK
```

### Doctor

- No data: `BLOCKED research data`, `MVP_STATUS=NOT_CONFIGURED`, exit 3.
- With configured valid data (E2E): all relevant checks PASS, exit 0.
- With `DATABASE_URL` pointing at isolated Postgres 16: `database PASS`, `migrations PASS 2/2`.
- With unreachable configured DB: `FAIL`, exit 1 (regression-tested).
- Malformed manifest: `FAIL`, exit 1 (regression-tested).

### E2E MVP acceptance (`tests/integration/test_mvp_e2e.py`, 15 tests)

Happy path: vendor CSV → builder → manifest → preflight PASS → dataset fingerprint → run-all →
3 families × 4 pinned configs = 12 trials all retained in `experiments.json` → per-family JSON+MD →
promotion artifacts → `MVP_RESEARCH_SUMMARY.{json,md}` → `report show`/`research status`/`status`/`doctor`.
Negative paths (all asserted): warnings-without-ack exit 4 and zero research performed; accepted
warnings persisted (dataset manifest, family known-biases, summary); missing membership exit 3 with
builder guidance; zero-exits manifest → preflight FAIL exit 2 and run-all refuses; bad OHLC → exit 2;
non-PIT `research run` refused (exit 2) without explicit opt-in; short history never APPROVEDs
(artifacts audited); **NO_STRATEGY_PROMOTED is exit 0**.

### PostgreSQL (isolated Docker `postgres:16`, port 55432)

```text
uv run pytest -m postgres          2 passed (migration idempotency/recovery, shadow roundtrip) — twice
doctor against it                  2/2 migrations reported correctly (after fixing stem/name bug)
```

### Quality gates

| Gate | Local (final) | CI PR #7 @ `9094ad1` (Python 3.12.14) |
|---|---|---|
| Tests (`-m "not external"`) | 383 passed, 2 skipped (no DATABASE_URL) | **386 passed, 0 failed**, 2 deselected |
| Coverage overall | 88.57% | **90.12%** (gate ≥80; baseline was 88.36%) |
| Critical coverage (oms/risk/reconciliation/authorization) | **94%** (gate ≥90) | pass |
| ruff check | clean | `All checks passed!` |
| ruff format | clean | `183 files already formatted` |
| mypy (src) | clean, 61 files | `Success: no issues found` |
| pip-audit | clean | `No known vulnerabilities found` |
| secret_scan + gitleaks | clean | pass |
| import smoke | clean | pass |
| CI history | — | two intermediate failures during the task, both fixed on-branch; **final push + PR checks green** |

Gates were never lowered (`--cov-fail-under=80`, critical `>=90` unchanged in `ci.yml`).

## Bugs found and fixes made

```text
P0  none (no live-authority or data-integrity bypass existed at baseline)

P1  trend_pullback family could never run: run_backtest evaluates the signal on
    1-bar history at the first decision day; the shipped candidates.py RAISED,
    aborting the whole family (the shipped script --family trend_pullback was
    therefore broken). Fixed: single-bar history returns rejected HOLD
    (conditions impossible), never a crash.
P1  Research crashed on bars carrying the gate-recommended `available_at` PIT
    column (candidate payload builder float(Timestamp)). Fixed: runner strips
    gate-only metadata at the research boundary.
P1  SAFETY: run_family_research persisted promotion decisions on every path —
    a survivorship-biased (non-PIT) run could write the APPROVED artifact kind
    the paper orchestrator trusts as eligibility. Fixed: non-PIT approvals are
    downgraded to RESEARCH_ONLY at the runner boundary; bidirectional
    regression test added (non-PIT cannot persist APPROVED; PIT wiring intact).

P2  doctor compared schema_migrations rows against migration STEMs while
    PostgresStore stores the `.sql` FILENAME — inverted status on real DBs.
P2  `data preflight` crashed (uncaught JSONDecodeError) on malformed manifests
    instead of failing closed with exit 2.
P2  .gitignore ignored `.env.example`, preventing the config contract from
    ever being shipped. Fixed; generated artifacts/ ignored with the
    artifacts/verification exception preserved.

(self-inflicted, caught by CI and fixed on-branch)
T   doctor/E2E assertions were ambient-DATABASE_URL order-dependent; made
    data-path doctor tests DB-independent.
```

## External blockers (not code-fixable, precisely scoped)

1. Licensed daily OHLCV bars + benchmark history (UTC, calendar-aligned).
2. Historical constituent membership **with removals** + delisted-symbol price
   coverage — the binding constraint; acceptance test is the builder's `exits>0`.
3. Elapsed forward evidence for shadow/paper (cannot be manufactured).
4. IBKR paper gateway connectivity/credentials.
5. Live authorization — permanent non-goal.

## Known limitations (honest surface)

- Turnover is **not measured** by the vector backtester; the summary publishes an
  explicitly-labelled trades/yr activity proxy plus a `metric_notes` disclosure.
- Corporate actions are assumed vendor-adjusted upstream; the dataset manifest
  records this as an unverified input (`corporate_actions.used=false`).
- The MVP E2E fixture proves the application workflow, **not alpha**; its
  verdicts are meaningless for trading.
- `ma_cross_baseline` is a registry control and is excluded from `run-all` by
  design (it remains available to the Phase-4 experiment harness).
- Postgres remains optional for MVP research (experiment/journal/promotion
  artifacts are file-durable by design); it is enforced by `doctor` only when
  configured.
- Local Python here is 3.14; CI's 3.12.14 is the reference environment
  (requires-python >=3.12; both green).

## Adversarial review outcomes (§24)

- **Product**: full happy path + all documented commands exercised by tests and
  on a fresh clone; exit-code contract documented in `docs/MVP1.md`; every
  blocker message names the next operator action.
- **Safety**: the P1 non-PIT-approval bypass found during this review was the
  headline fix; import-graph, live-flag, secret-leak and warning-acknowledgement
  boundaries all have regression tests; `NO_STRATEGY_PROMOTED` remains the
  actual observed state everywhere.
- **Reproducibility**: every artifact pins commit + dataset fingerprint +
  policy hash + grids + cost model + bootstrap seed; clean-machine install
  proven; CI is the independent clean-environment proof.
- **The Fool**: corrected over-trust in three places — infrastructure proof vs
  strategy proof separated in docs/tests/report; no readiness claim hides the
  data blocker (`doctor` deliberately exits non-zero); turnover silence
  converted into a labelled proxy; removed any route by which "convenience"
  (wrappers, run-all, exploratory mode) could mint eligibility.

## Readiness

```text
MVP1_CODE_READY=YES
MVP1_OPERATIONAL_RESEARCH=REQUIRES_EXTERNAL_DATA
NO_STRATEGY_PROMOTED
LIVE=NOT_AUTHORIZED
MVP1_READINESS = READY (engineering) — per MVP1_READINESS.md matrix
```

## Exact next operator action

1. Human review + merge decision on PR #7 (CI green at `9094ad1`; do not merge
   without review).
2. Procure the data bundle specified in `docs/DATA_SOURCING.md` (the membership
   history **with exits** + delisted prices is the long-lead item).
3. On delivery: `uv run trading-platform data build-membership …` → `data
   preflight …` → resolve FAIL or acknowledge warnings → `research run-all …`
   → read `artifacts/research/<run-id>/MVP_RESEARCH_SUMMARY.md`.
   If it says NO STRATEGY PROMOTED — that is the MVP working as designed.
