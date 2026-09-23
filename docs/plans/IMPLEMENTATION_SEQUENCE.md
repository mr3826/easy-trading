# Implementation Sequence — integration/remaining-platform-work

## Wave 0 — baseline and design (orchestrator)

1. Fetch remotes, verify branch SHAs and PR state. DONE — `origin/main`=`323a701`, PR head `37ebec9` verified, PR OPEN/MERGEABLE.
2. Reproduce baseline in isolated worktree with ephemeral PostgreSQL 16 service. DONE — 74 passed, 85% overall, 90% critical, all static gates exit 0.
3. Source audits (two independent read-only audits). DONE — all 10 suspected gaps verified with file:line evidence.
4. Event-flow trace. DONE — chain breaks documented (data dead-end, simulator→OMS unconnected, reconciliation placeholder-fed, persistence unwired).
5. Documents created: `REQUIREMENT_MATRIX.md`, `SOURCE_PHASE_MAP.md`, `FILE_OWNERSHIP.md`, `RISK_REGISTER.md`, `FOUNDER_HANDOFF_QUEUE.md`, `INTERFACE_CONTRACTS.md`, `IMPLEMENTATION_SEQUENCE.md`, ADR-007.
6. Orchestrator baseline-prep commit (before parallel launch): simulator unblocking fixes (public seed, `realized_pnl` in FILL detail, duplicate-snapshot fix, MARKET fills at next open, dead-code removal), stray `risk__init__.py` removal, CWD-relative test-path fix, `ib_async` pinned centrally.
7. Interfaces shared by multiple workers resolved before parallel edits begin.

## Wave 1 — parallel implementation (six workers, isolated worktrees)

Launch order (all parallel; non-overlapping ownership):

| Worker | Branch | Focus |
|---|---|---|
| A | `agent-a-market-data` | domain freeze + property tests, real Parquet, point-in-time, vendor-neutral interface |
| B | `agent-b-research` | real hashes, walk-forward as-of + robustness, baseline report fixes |
| C | `agent-c-oms-risk` | ledger-derived reconciliation, fill/startup/reconnect reconciliation, idempotency, fail-closed chain, Hypothesis risk tests |
| D | `agent-d-shadow-ops` | genuine shadow orchestrator, archival/replay, real monitoring checks, chaos, backup/restore |
| E | `agent-e-ibkr-paper` | IBKR client library, two-flag paper boundary, external smoke harness |
| F | `agent-f-ml-llm` | trainable ranking model, promotion enforcement, LLM fail-closed, forward recorder |

Each worker: tests first, small reviewable commits, handoff block
(AGENT/BRANCH/START_SHA/FINAL_SHA/FILES_CHANGED/REQUIREMENTS_COMPLETED/TESTS_ADDED/
COMMANDS_AND_EXIT_CODES/COVERAGE/SECURITY_CONSIDERATIONS/OPEN_RISKS/
DEPENDENCY_REQUESTS/EXTERNAL_GATES).

## Wave 2 — integration (orchestrator)

1. Review every diff; reject placeholder implementations.
2. Integrate one logical group at a time: A (data/domain) → B (research) → C (oms/risk/reconciliation) → D (shadow/monitoring) → E (broker) → F (ml/llm).
3. Run impacted tests after each integration; full suite after every major merge.
4. Reconcile dependency and schema changes centrally.
5. Verify broker/shadow/OMS/reconciliation contracts connect (C1-C6).
6. Resolve duplicated implementations (CorporateAction, SystemMonitor).

## Wave 3 — independent falsification review (six reviewer roles)

Architecture, code, security, test-quality, database/recovery, trading-safety.
Actively try to disprove: no-lookahead, idempotency, recovery equivalence,
risk-limit enforcement, reconciliation independence, shadow isolation,
paper/live separation, AI fail-closed behavior, evidence authenticity.
Every high/critical finding fixed and retested.

## Wave 4 — evidence and pull request

Full gate suite (uv sync/import/ruff/format/mypy/pytest+coverage/pip-audit/secret
scan + PostgreSQL service). Evidence generated from the final source commit.
Push `integration/remaining-platform-work`; open a new PR. PR #1 is never merged
or modified automatically.
