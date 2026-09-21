# Authoritative Requirement Matrix — integration/remaining-platform-work

Base: verified PR head `37ebec9`. Evidence: Wave 0 source audits (two independent
read-only audits) + local baseline reproduction on 2026-09-21.

## Baseline reproduction (verified, not assumed)

| Claim | Verified result |
|---|---|
| PR #1 open and unmerged | CONFIRMED — state OPEN, MERGEABLE, head `37ebec9` |
| CI 74 passing tests | CONFIRMED with PG service: 74 passed, 0 skipped (locally without PG: 73 passed, 1 skipped) |
| Coverage ~85.20% | CONFIRMED with PG service: 85% overall (82% without PG) |
| Critical-module coverage ~90% | CONFIRMED: 90% aggregate for oms/risk/reconciliation/authorization |
| Ruff, format, mypy, pip-audit, secret scan, imports pass | CONFIRMED, all exit 0 |
| `main` on older prototype | CONFIRMED — `origin/main` = `323a701`, untouched |
| Branch is offline foundation, not operational system | CONFIRMED — every operational chain is a dead-end in src (see event-flow audit) |
| Live trading unauthorized | CONFIRMED — `authorize_live()` always returns disapproved; config neutralizes enabling flags |

## Requirement matrix (R = requirement ID, Owner, Verified state, Required work)

| ID | Requirement | Owner | Source state (audited) | Required work |
|---|---|---|---|---|
| R1 | Domain immutability and invariants | A | Validation solid; value objects mutable (not frozen); only JournalEvent frozen | Freeze value objects; keep validated stateful transitions; property tests for prices, quantities, events, timestamps |
| R2 | Real Parquet reading/writing | A | `get_bars` returns `[]` (TODO); `load_parquet_data` reads via pandas but nothing converts to Bar; no pyarrow import | Implement pyarrow read/write, Bar conversion, deterministic metadata + content SHA-256 checksums |
| R3 | Point-in-time filtering | A | Contract-only; no as-of enforcement; duplicates/stale downgraded to warnings | Enforce as-of; reject duplicates; detect missing/stale/revised; fail closed on corruption |
| R4 | Raw vs normalized separation + vendor-neutral interface | A | Raw/adjusted enum exists; ingestion only reachable from tests | Preserve raw inputs separately; FakeMarketDataProvider + deterministic fixtures; corporate-action/symbol-history boundaries |
| R5 | Genuine experiment hashes | B | `compute_code_hash()` returns `"placeholder_code_commit_hash"`; no policy hash; dependency_lock unvalidated | Real git SHA, dependency-lock hash, dataset hash, policy hash |
| R6 | Malformed experiment records rejected | B | `except Exception: pass` silently skips | Reject malformed records; fix baseline report placeholders (`max_drawdown/turnover/seed: None`); fix broken `realized_pnl` contract |
| R7 | Walk-forward validation complete | B | Signal generation uses future bars (SMA window anchored at period end); signal/bar misalignment; every fold re-evaluates same locked window; bootstrap buggy; `_check_determinism` stub | As-of signal generation; per-symbol signal application; parameter stability; cost/slippage sensitivity; turnover/drawdown/exposure analysis; bootstrap/Monte Carlo where appropriate |
| R8 | OMS durable ledger + append-only | C | In-memory mutable list; `clear_event_ledger` wipe; DB trigger append-only (real) | Enforce append-only journal semantics; no history wipes; transactional state changes |
| R9 | Idempotency + unique order constraints | C | DB unique constraints real; OMS hole: duplicate while SUBMITTED silently re-accepted; late fills dropped silently | Fix idempotency hole; dedup duplicate fills; incident on late fills; averaged partial-fill price; PARTIALLY_FILLED status used |
| R10 | Reconciliation from durable ledger events | C | `reconcile_cash` uses `total_commission = 1.0 # placeholder`; startup reconciles hardcoded 10000.0 placeholders; `on_fill_event` is a stub returning a placeholder note | Derive cash/fees/fills/positions from durable ledger events; fill/startup/reconnect/periodic/session-end reconciliation |
| R11 | Independent-state reconciliation | C | Broker snapshot = copied OMS state (`_orders` seeded from `oms.orders`); `BrokerSnapshot` unused; tests compare empty dicts | Reconcile independently obtained internal vs broker snapshots; tests must not compare duplicated copies |
| R12 | Fail-closed incident chain | C | `blocks_new_orders` set but never consumed; `record_incident` only called by tests; no alert on mismatch; `OPERATOR_RESOLUTION_REQUIRED` nowhere | MISMATCH → BLOCK_NEW_ORDERS → PERSIST_INCIDENT → CRITICAL_ALERT → OPERATOR_RESOLUTION_REQUIRED, wired end-to-end |
| R13 | Hard-risk property tests | C | Zero Hypothesis tests anywhere; all risk-limit tests example-based | Hypothesis tests: generated orders cannot breach cash, exposure, position-count, loss, drawdown limits; protective-order invariants |
| R14 | Genuine shadow orchestrator | D | shadow.py = 23-stmt sink; no ingestion, archival, risk, persistence, replay; monitor.py claims replay comparison with no implementation | Completed-bar consumption, input archival, production-equivalent strategy+risk, WOULD_SUBMIT persistence, structural submission impossibility, deterministic replay comparison |
| R15 | Real monitoring checks + alert transports | D | DB/data checks caller-fed; no disk check; AlertHandler defaults = two prints to same stderr; no Telegram config | Real DB/storage/journal-lag/disk checks; independent alert transport interfaces + fakes; email/webhook/Telegram configs without secrets |
| R16 | Chaos + recovery + backup/restore | D | Chaos injects only into wrapped callables; FAILURE_DISK_PRESSURE no-op; backup encryption real; clean-room restore absent; unreachable reconciliation branch + fake latency in shadow operator | Failure injection into real injectable dependencies; 7 failure scenarios covered; encrypted backup + checksum; clean-room restore procedure; external dead-man config |
| R17 | IBKR paper adapter | E | Safe boundary only; no client library; one flag; no lifecycle; Alpaca skeleton placeholder; cancel/list on OMS-seeded dict | Pin documented client library (ADR-008); full lifecycle/callbacks; two-flag gate; live port/account rejection; paper-only external smoke harness; reconnect recovery feeding reconciliation |
| R18 | ML ranking after deterministic baselines | F | Chronological split real; closed-form fit real; hashes real; promotion blocking is data-only | Actual trainable ranking model; deterministic baseline ranking; feature-schema and model hashes; promotion criteria + enforcement (no auto-promotion from one metric); deterministic fallback |
| R19 | LLM feature fail-closed | F | Strict schema real; no timeouts; staleness partial; malformed injection-pattern list; registry `is_feature_leakage` stub | Reject malformed JSON, NaN, infinity, unknown symbols, stale content, contradictory output, prompt injection, timeouts, upstream failures → `FEATURE_REJECTED`; forward-test recorder without claiming results |
| R20 | Test integrity | C + Orchestrator | `test_phase6_integration.py:6` hard-codes absolute root `sys.path`; `test_migrations_and_imports.py:10` CWD-relative path; tautological assertions (secret redaction asserts raw storage, idempotency tautology, placeholder domain tests) | Portable imports; meaningful assertions; remove encoded-behavior holes |
| R21 | No-lookahead end-to-end | Orchestrator + B | Simulator deferral correct; MARKET mode fills same-bar close (reachable lookahead); walk-forward generates signals from future bars | MARKET fills at next open; as-of signal generation; end-to-end no-lookahead tests |
| R22 | Live trading permanently disabled | Orchestrator | Fail-closed confirmed; CLI prints fixed safe values | Keep permanent restrictions; no-live-authority tests |
| R23 | CI + evidence | Orchestrator | CI uses uv, PG 16, coverage 80/90 gates, Gitleaks, artifacts | Maintain; evidence from final source commit |
