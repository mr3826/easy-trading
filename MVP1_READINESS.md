# MVP-1 Readiness Matrix

Verified on branch `feature/mvp1-readiness` (starting baseline `f854ae1` on
`main`). Evidence pointers reference concrete artifacts/tests, not claims.

Statuses: `READY` | `READY_WITH_EXTERNAL_INPUT` | `BLOCKED` | `NOT_REQUIRED_MVP1` | `NOT_AUTHORIZED`

| Area | Status | Evidence | Blocker |
|---|---|---|---|
| Install | READY | `uv sync --all-extras --locked` green (local + CI); `uv.lock` pinned; clean-machine re-verification recorded in `MVP1_FINAL_REPORT.md` | — |
| Configuration | READY | `.env.example`, `trading-platform doctor` validates config; unsafe live values force-rejected by `config.load_config()` (regression-tested in `test_cli_safety.py`) | — |
| CLI | READY | one canonical `trading-platform` surface: `status`, `doctor`, `data build-membership/preflight`, `research list-strategies/run/run-all/status/latest`, `report show`; legacy `--status` preserved; legacy scripts are wrappers | — |
| PostgreSQL | READY | migrations `0001/0002` + `test_postgres_store.py` idempotency/recovery pass in CI service container (`postgres:16`) | local isolated DB run recorded in final report |
| Data builder | READY | `data build-membership` + round-trip validation + zero-exit warning; `test_data_quality.py`, E2E `TestFailClosed` | — |
| PIT preflight | READY | fail-closed gate (missing manifest/zero exits/static membership/OHLC/NaN/calendar/timestamps); E2E proves exit codes 0/1/2/3 and that FAIL blocks research | — |
| Research runner | READY | `research run` (gated) + `run-all`; per-family JSON+MD artifacts; every trial retained (`experiments.json`, E2E asserts 12/12) | — |
| Validation | READY | purged walk-forward, cost stress 1×/1.5×/2×, PSR/DSR, CSCV-PBO, bootstrap, white reality check, parameter stability, concentration, regime decomposition — unit + property tests | — |
| Promotion gate | READY | policy-versioned, immutable artifacts, multi-evidence AND, fails closed on missing evidence (E2E: short history never APPROVEDs; decision artifacts audited) | — |
| Reports | READY | `MVP_RESEARCH_SUMMARY.{md,json}` with traceability fields, rejection reasons, known biases, evidence ceiling, next action; `report show` | — |
| Safety | READY | live stays `NOT_AUTHORIZED` under env attacks; CLI imports no broker/OMS/risk modules (subprocess assertion); warning acknowledgement explicit + persisted; E2E proves no bypass | — |
| CI | READY | branch/PR `Verification` workflow green expected; gates unchanged (cov≥80, critical≥90, ruff/format/mypy/pip-audit/gitleaks) | final PR run must be green before merge |
| Docs | READY | `docs/MVP1.md`, README, CURRENT_STATE, TESTING, DATA_SOURCING reconciled; exit codes documented | — |
| External market data | BLOCKED | `doctor` → `MVP_STATUS=REQUIRES_EXTERNAL_DATA`; requirements precise in `docs/DATA_SOURCING.md` | licensed PIT bars + membership-with-exits + delistings (vendor procurement) |
| Shadow readiness | READY_WITH_EXTERNAL_INPUT | shadow machinery complete + tested; entry requires an APPROVED promotion artifact (none exists) + forward evidence | no promoted strategy; elapsed forward time |
| IBKR paper | BLOCKED | adapter complete/unit-tested; connect-only smoke gated | real paper gateway, credentials, elapsed evidence |
| Live | NOT_AUTHORIZED | permanent repository policy; authorization path rejects unconditionally; no CLI/CI/env route exists | policy — out of scope forever for this task |

## Roll-up (code-verifiable)

```text
MVP1_CODE_READY=YES
MVP1_OPERATIONAL_RESEARCH=REQUIRES_EXTERNAL_DATA
NO_STRATEGY_PROMOTED
LIVE=NOT_AUTHORIZED
```

Merging this branch after green CI completes MVP-1 engineering readiness;
MVP-1 *operational* completion then depends solely on licensed external
point-in-time data.
