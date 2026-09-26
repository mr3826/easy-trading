# MVP-1 Baseline Audit

Audited on branch `feature/mvp1-readiness`, created from `main`.

## Verified starting state (measured, not assumed)

| Item | Claimed | Measured |
|---|---|---|
| `main` HEAD | `f854ae1104d80546e48747f1fe83aa77e310e7a1` | `f854ae1104d80546e48747f1fe83aa77e310e7a1` — matches |
| Working tree | — | Clean except untracked local debug scratch (`mypy_*.txt`, `ruff_*.txt`, `fix_*/analyze_*/check_*/extract_*/find_*` scripts, `.coverage`, `.kilo/`) — never committed |
| Tests (`-m "not external"`) | 334 passed | 336 collected → 334 passed + 2 postgres-dependent skipped locally — consistent |
| Coverage | ~88.36% | TOTAL 7219 stmts / 840 miss = 88% (gates: >=80 overall, >=90 critical) |
| CI | — | Latest `Verification` run on `main` **success**; PRs #1–#6 all merged with green CI |
| ruff check / format | — | All checks passed / 172 files already formatted |
| mypy `trading-platform/src` | — | Success, no issues (54 files) |
| pip-audit | — | No known vulnerabilities |
| secret scan (`scripts/secret_scan.py`) | — | Clean |
| Live boundary | NOT_AUTHORIZED | `config.load_config()` force-rejects `LIVE_TRADING_ENABLED=true`; `authorization.authorize_live()` ends in a permanent policy denial even when every gate passes |
| Strategy status | none promoted | No approved promotion artifacts under any tracked path; promotion gate fails closed on missing evidence |

Latest merged PRs: #6 membership builder + vendor spec, #5 PIT preflight gate,
#4 alpha validation/reverification, #3 platform hardening, #1 verified foundation.

## What actually exists (Spec Miner reverse-engineering)

Strong engine modules under `trading-platform/src/trading_platform/`:

- `research/data_quality.py` — PIT membership manifest schema + builder from vendor
  CSV + fail-closed preflight (`PASS`/`PASS_WITH_WARNINGS`/`FAIL`, versioned).
- `research/runner.py` — full per-family reverification: features → regimes → grid →
  purged walk-forward → cost stress → PSR/DSR/PBO/bootstrap/concentration/regimes →
  promotion → JSON+MD artifacts, `dataset_hash` over loaded frames, git commit pin,
  PIT membership support that lifts the survivorship evidence ceiling.
- `validation/` — statistics (PSR, DSR, CSCV-PBO, stationary bootstrap, white reality
  check), purged/embargoed splits, cost stress, parameter stability, concentration.
- `promotion/` — versioned policy gate, immutable decision artifacts, policy-hash-bound
  approval loading (`load_approvals`), `REQUIRES_EXTERNAL_SETUP`-style fail-closed paths.
- `strategies/candidates.py` — registry `STRATEGY_FAMILIES`: `trend_relative_strength`,
  `breakout_volume`, `trend_pullback` researchable; `ma_cross_baseline` registered as a
  control (signal `None`, cannot be run through the family runner).
- `persistence/` — experiment records with provenance-hash validation, Postgres store +
  migrations (`trading-platform/migrations/0001_core.sql`, `0002_shadow_decisions.sql`),
  shadow archive, journaling elsewhere.
- Execution/risk/OMS/shadow/paper machinery — deliberately out of MVP-1 scope; all
  correctly gated behind promotion + authorization and unreachable from research.

## The dominant product gap (confirmed)

The installed `trading-platform` command is 20 lines that only print
`LIVE_TRADING_ENABLED=false / LIVE_STATUS=NOT_AUTHORIZED` (`--status`). Every real
workflow lives in a separate root script with its own conventions:

1. `scripts/build_membership_manifest.py` (`--csv --output --source`)
2. `scripts/run_data_preflight.py` (`--data-dir --benchmark --membership --output`,
   exit codes 0/1/2/3)
3. `scripts/run_strategy_research.py` (`--family --data-dir --benchmark --symbols
   --output-dir`, hardcoded per-family grids **duplicated only inside the script**)

An operator must know all three, their differing flags, differing exit-code meanings,
and manually thread: manifest → preflight verdict → warning acknowledgement → dataset
identity → family list → cross-family conclusion. There is **no**:

- one canonical CLI (`data …`, `research …`, `doctor`, meaningful `status`)
- `research run-all` across registered families
- warning-acknowledgement gate (`PASS_WITH_WARNINGS` currently stops nothing)
- canonical dataset manifest/fingerprint at file level (bar checksums, provenance,
  verdict, accepted warnings) — `runner._dataset_hash` only hashes loaded frames post-hoc
- cross-family MVP summary artifact (`MVP_RESEARCH_SUMMARY.*`)
- high-level product state (`status` today says nothing about where the project stands)
- `.env.example` (and `.gitignore` actively ignores `.env.example` — a bug)
- E2E test of the operator workflow (tests prove modules, not the product path)

## Stale / incomplete documentation

- `README.md`: "coding-verifiable foundation is in progress" — understates verified
  state; quick start does not mention the real data workflow.
- `docs/TESTING.md`: says "321 passed" (stale vs 334).
- `docs/DATA_SOURCING.md`: accurate but script-only; needs the CLI form.
- `remaining_todos.md` / `COMPLETION_AUDIT.md`: accurate on external blockers; no
  MVP-1 product-surface framing.
- No `docs/MVP1.md`.

## External blockers (cannot be fixed in code)

| Blocker | Owner |
|---|---|
| Licensed daily OHLCV bars + benchmark history | vendor procurement |
| Historical constituent membership **with exits** + delisted price coverage | vendor procurement |
| 60+ trading days forward paper evidence | elapsed time |
| IBKR paper gateway connectivity/credentials | external setup |
| Live authorization | permanent policy — NOT_AUTHORIZED |

## Verdict

Engineering foundation: strong and verified. Product surface: fragmented — the #1
MVP-1 gap is a single coherent operator CLI over the existing services, with the
data-quality gate, dataset identity, run-all workflow, MVP summary, `doctor`, and a
real `status` wired on top of them — without weakening any safety boundary.
