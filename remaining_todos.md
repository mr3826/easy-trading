# Remaining Work — Reconciled 2026-09-25

Legend:

- `[x]` implemented and verified (code + tests green in CI)
- `[~]` implemented but external evidence pending (needs real data, elapsed forward
  time, credentials, or a human operator action — preparatory code is complete)
- `[ ]` genuinely unfinished

**The authoritative per-item evidence table is [docs/TASK_RECONCILIATION.md](docs/TASK_RECONCILIATION.md).**
This file is the short operational view; do not treat unchecked `[~]` items as code failures.

## Phase 7 — Failure handling, security, backup, recovery

- [x] Failure injection: internet/DB loss, process kill, clock skew, stale quote,
      duplicate events, broker reject, partial/late fills, disk pressure, restart
      during open orders (chaos_engine + monitoring/recovery tests)
- [x] Threat model documented (external pentest: operator scope)
- [~] Firewall/private broker ports, env separation at deploy time (operator action;
      repo enforces config boundaries + secret scanning in CI)
- [x] Encrypted, checksummed backups (restore drill itself: [~] operator action)
- [x] Runbooks authored (execution-at-least-once: [~] operator action)
- [~] Dead-man heartbeat deployed OUTSIDE the VM failure domain (code + CLI complete)
- [x] Capital-safety invariants survive every mandatory failure scenario (tested)
- [x] CI has no live trade authority (enforced + artifact evidence)
- [x] Secret scan passes (gitleaks + scripts/secret_scan.py)

## Phase 8 — Shadow mode

- [x] Bar-completion validation, archival, WOULD_SUBMIT with full risk decision,
      structural inability to submit broker orders (tested)
- [x] Deterministic replay + mismatch persistence/escalation (tested)
- [x] Monitoring metrics: heartbeat, freshness, latency, rejections, journal lag,
      reconciliation state (tested)
- [~] Dedicated VM deployment, two independent live alert channels (external setup)
- [~] Zero unexplained differences / zero stale decisions / zero duplicate
      hypotheticals over real sessions (REQUIRES_FORWARD_EVIDENCE)

## Phase 9 — IBKR paper

- [x] Adapter complete: paper-only validation, lifecycle, IDs, acks, partial fills,
      commissions, rejects, cancel/replace, brackets/OCA, snapshots, ledger fallback,
      startup/reconnect/fill/periodic/session-end reconciliation (all unit-tested)
- [~] Real gateway connectivity, manual authentication, observed protective-order
      behavior (REQUIRES_EXTERNAL_SETUP — connect-only smoke test gated)
- [~] G9 exit criteria on real paper sessions (REQUIRES_FORWARD_EVIDENCE)

## Phase 10 — Paper evidence (60 trading days)

- [x] PaperEvidenceTracker: records and auto-answers every G10 question
      (sessions, trades, regimes, duplicates, unresolved fills, stale trades,
      slippage, sizing deviation, rejects, partial fills, divergences, minimum
      sample query)
- [~] The 60+ days themselves (REQUIRES_FORWARD_EVIDENCE — cannot and will not be
      faked)

## Phase 11 — ML / LLM

- [x] ML ranks eligible candidates only; leakage guards, provenance, deterministic
      fallback, BASELINE vs BASELINE+RANKER comparison (tested)
- [x] LLM: strict schema, injection rejection, no broker/OMS/risk authority,
      PIT-provenance policy (tested)
- [~] G11 durable-benefit determination (REQUIRES_FORWARD_EVIDENCE)

## Phase 12 — Live

- [ ] NOT AUTHORIZED, permanently. All Phase 12 remains human governance outside this
      code. `docs/LIVE_READINESS_GATES.md` records what would have to be true before
      the question could even be raised.

## Genuine unfinished engineering (what [ ] should really mean)

Nothing code-verifiable is known outstanding. Remaining work is: vendor data
onboarding, PIT constituent membership feed, operator drills/deployments, and
elapsed shadow/paper evidence.
