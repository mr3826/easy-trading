# Task Reconciliation — Authoritative Remaining-Work Inventory

Reconciled: 2026-09-25 against branch `feature/alpha-validation-autonomous-paper`.
This supersedes `remaining_todos.md` checkboxes (which were stale: Phases 7–11 were
marked entirely unfinished while substantial, tested implementations existed).

Status vocabulary: `COMPLETE_VERIFIED` (code + tests green in CI) · `COMPLETE_CODE_ONLY`
(implementation exists, meaningful verification requires external systems/time) ·
`PARTIAL` · `MISSING` · `REQUIRES_EXTERNAL_SETUP` · `REQUIRES_FORWARD_EVIDENCE` ·
`REQUIRES_OPERATOR_ACTION` · `NOT_AUTHORIZED` · `NOT_APPLICABLE`.

## Phase 7 — Failure injection, security, backup, recovery

| Task | Status | Evidence | Remaining gap |
|---|---|---|---|
| Inject failures: internet/DB loss, process kill, clock skew, stale quote, duplicate event, broker reject, partial/late fill, disk pressure, restart during open orders | COMPLETE_VERIFIED | `chaos_engine.py`; `test_monitoring_recovery.py` (chaos_* tests), `test_oms_reconciliation.py` (late/over-fill, rebuild), `test_broker_adapter_paper.py`; OMS ledger replay | none in code |
| Threat model | COMPLETE_CODE_ONLY | `docs/threat-model.md` | external pentest is operator scope |
| Broker API private/firewalled DB ports | REQUIRES_OPERATOR_ACTION | documented in `threat-model.md`, runbooks | host/network config can't be proven from repo |
| Runtime secret injection, env separation | COMPLETE_VERIFIED | `.env` pattern (`environment.md`), no committed credentials (gitleaks CI, `secret-scan.txt`) | operator must keep it true at deploy |
| Encrypted backups + clean-room restore drill | COMPLETE_VERIFIED (code) / REQUIRES_OPERATOR_ACTION (drill) | `backup.py` round-trip + tamper tests | actual restore drill on clean machine |
| Recovery runbooks (disconnect, stale data, mismatch, missing protection, DB failure, gateway auth, emergency stop) | COMPLETE_CODE_ONLY | `runbooks/incident-response.md`, `backup-restore.md`, `shadow-operator-guide.md`, `ibkr-paper-guide.md`, `alert-setup.md`, `docs/OPERATIONS_RUNBOOK.md` | "executed at least once" = operator action |
| External dead-man heartbeat outside VM failure domain | PARTIAL | `dead_man.py` CLI + alert path + tests | remote/host deployment REQUIRES_EXTERNAL_SETUP |
| Capital-safety invariants survive every failure scenario | COMPLETE_VERIFIED | safety/property tests + fail-closed chains (`test_safety_boundaries.py`, `test_risk_properties.py`) | — |
| VM/process death triggers external alert | COMPLETE_CODE_ONLY | `DeadManHeartbeat.alert_transport` test; webhook/telegram/email channels | real endpoint delivery REQUIRES_EXTERNAL_SETUP |
| Backup restored & validated, not just created | REQUIRES_OPERATOR_ACTION | round-trip restore test exists | human drill record |
| CI has no live trade authority | COMPLETE_VERIFIED | `ci.yml` forces `LIVE_TRADING_ENABLED=false`, `no-live-authority-test.txt` artifact | — |
| Secret scan and security review pass | COMPLETE_VERIFIED (scan) / COMPLETE_CODE_ONLY (review) | gitleaks step, `secret_scan.py`, `test_safety_boundaries.py` | independent human review recommended |

## Phase 8 — Shadow mode

| Task | Status | Evidence | Remaining gap |
|---|---|---|---|
| Live/delayed data seam, bar-completion validation, archival | COMPLETE_VERIFIED | `shadow.py` (missing slots, revised/invalid bars, raw archive tests) | real feed = external |
| WOULD_SUBMIT + full risk decision, never broker submit | COMPLETE_VERIFIED | `test_structural_submission_impossibility`, `test_risk_gated_would_submit` | — |
| Live-vs-replay comparison | COMPLETE_CODE_ONLY | deterministic replay + mismatch escalation tests | real-data divergence = forward evidence |
| Dedicated Linux VM deployment, no public broker port | REQUIRES_EXTERNAL_SETUP | docs/runbooks | host provisioning |
| Metrics: heartbeat, freshness, latency, signals, rejections, reconciliation, DB, journal lag | COMPLETE_VERIFIED | `monitor.py` + tests | — |
| Two independent critical alert channels | COMPLETE_CODE_ONLY | default channels test (file-based independence) | real endpoints external |
| Daily review / weekly discrepancy report | REQUIRES_OPERATOR_ACTION | runbook defined | human routine |
| Exit-gate G8 criteria (zero unexplained differences, zero stale decisions, zero duplicate hypotheticals, session completeness, dead-man proven, minimum forward sample) | REQUIRES_FORWARD_EVIDENCE | machinery blocks/records/escalates each class | elapsed shadow operation |

## Phase 9 — IBKR paper

| Task | Status | Evidence | Remaining gap |
|---|---|---|---|
| Prerequisites (account, subscriptions, manual auth) | REQUIRES_EXTERNAL_SETUP | `ibkr-paper-guide.md` | operator IBKR setup |
| Adapter without IBKR objects leaking into core | COMPLETE_VERIFIED | `broker/base.py` protocol; no module-level ib_async import test | — |
| Lifecycle/IDs/acks/parent-child/OCA/reject/cancel/replace/fills/snapshots | COMPLETE_VERIFIED | `test_broker_adapter_paper.py` (30+ tests, mocked client) | real-gateway timing = external |
| Reconcile startup/reconnect/fills/timer/session-end | COMPLETE_VERIFIED | `test_oms_reconciliation.py` session hooks + `risk.ReconciliationEngine` | — |
| Record paper-simulator limitations | COMPLETE_CODE_ONLY | `ibkr-paper-guide.md`, `LIMITATIONS.md` | — |
| Manual auth + fail-closed disconnect tested | COMPLETE_CODE_ONLY | disconnect path tests | live TWS auth = operator |
| LIVE_TRADING_ENABLED=false, separate paper config | COMPLETE_VERIFIED | paper-only gates (`validate_paper_target`), rejection tests, `config.py` reset | — |
| G9 exit criteria (zero unexplained positions/fills, 100% state recovery on restart, protective-order observation, disconnect alerts externally) | REQUIRES_FORWARD_EVIDENCE | all mechanisms tested | real paper running |

## Phase 10 — Extended paper validation (60 days)

| Task | Status | Evidence |
|---|---|---|
| Record capability: sessions, signals, regimes, duplicates, unresolved fills, risk violations, stale trades, reconnects, restarts, divergences, realized-vs-modeled slippage/cost, sizing deviation, turnover, drawdown, rejects, partial fills | COMPLETE_VERIFIED | `monitoring/paper_evidence.py` `PaperEvidenceTracker` + `test_paper_evidence.py`; orchestrator journal; reconciliation incidents |
| Actual 60+ days of paper evidence, regime coverage, zero violations | REQUIRES_FORWARD_EVIDENCE | tracker `report()` answers automatically; `status=REQUIRES_FORWARD_EVIDENCE` until events exist. **No code path may mark this passed.** |

## Phase 11 — ML ranking / LLM news

| Task | Status | Evidence |
|---|---|---|
| ML ranks only, never eligibility/risk/limits/authorization | COMPLETE_VERIFIED | `test_ml_pipeline_ranking.py`, `test_research_robustness.py`; rankers feed only order among eligible candidates |
| Chronological labels, leakage prevention, stale/malformed rejection, deterministic fallback | COMPLETE_VERIFIED | `ml_pipeline.py` split ordering, `ModelInputRejected`, registry validation; `ml-feature-safety.md` |
| Provenance: dataset hash, commit, training period, features, hyperparameters, metrics | COMPLETE_VERIFIED | `ModelArtifact` fields + tests |
| BASELINE vs BASELINE+RANKER identical conditions | COMPLETE_CODE_ONLY | `MLRanker.compare`; identical data/costs in reports | 
| LLM: no broker/credentials/OMS/risk authority; strict schema; provenance; prompt-injection rejection; deterministic fallback | COMPLETE_VERIFIED | `features/__init__.py` parser, `ml_ranking.LLMSentimentFeature` validator/injection tests |
| Historical LLM news not promotion-eligible unless PIT | COMPLETE_CODE_ONLY | policy documented; promotion pipeline excludes LLM evidence |
| G11 benefit determination | REQUIRES_FORWARD_EVIDENCE | requires real-data OOS comparison |

## Phase 12 — Live work

| Task | Status |
|---|---|
| Everything involving live capital/accounts/credentials | **NOT_AUTHORIZED** (permanent fail-closed policy) |
| Documentation of future gates, preflight/authorization-state modeling, evidence schema | COMPLETE_CODE_ONLY (`authorization.py`, `LIVE_READINESS_GATES.md`) |

## Root backlog summary (phases 0–6, per prior audit)

Items 1–7, 9–12 of the "Immediate first backlog": COMPLETE_VERIFIED (CI artifacts under
`artifacts/verification/`). Item 8 (vendor spike): REQUIRES_EXTERNAL_SETUP — vendor data
universe ingestion remains operator work; the pipeline accepts Parquet today.

## Bugs found and fixed during this reconciliation (P0/P4)

1. **Trailing-stop same-bar lookahead** in `research/backtest.py` — stop ratcheted from the
   current bar's close/ATR was evaluated against the same bar's low. Fixed: exits evaluated
   with the stop knowable at the bar open; ratchet applies to the next bar.
   Regression test `test_backtest_no_trailing_stop_same_bar_lookahead`.
2. **Entry sizing priced against stale signal close, cash clamp ignored commission** —
   research sizing now sizes at the actual fill price, reserves the entry commission, and
   caps notional at 20% of equity (parity with `SizingPolicy`).
3. **Production cash clamp overdraw risk** — `size_position` now subtracts
   `commission_per_order` inside the settled-cash bound (property + unit tests).
4. **DSR fallback scale used wrong units** for the annualized Sharpe std — corrected
   (`periods*(1+SR_daily²/2)/(T−1)`); regression test.
5. **PBO could crash or over-trust thin data** — `effective_pbo_blocks` (≥5 rows/block)
   with fail-closed `INSUFFICIENT_DATA` reporting instead of exception/silent pass.
6. **Orchestrator had no mid-cycle protection** — kill switch + promotion artifact are
   re-verified immediately before every submission (tests for operator latch and approval
   deletion mid-cycle).
7. **Runner silently wrote promotion artifacts to CWD** — explicit `promotions_root`
   parameter now threaded through (tests use tmp paths).

## Follow-up (2026-09-26): dataset point-in-time preflight gate

The next milestone is data quality before strategy performance. Delivered code-verifiable
preparation: `research/data_quality.py` + `scripts/run_data_preflight.py` — a fail-closed
gate that refuses datasets without a point-in-time constituent membership manifest, and
flags survivorship contamination (no exits, static membership, adds-only growth) plus bar
integrity failures. `run_family_research` accepts the same manifest
(`universe_membership=`) to lift the survivorship evidence ceiling legitimately.
Still REQUIRES_EXTERNAL_SETUP: the real bars + the real historical membership feed itself.
