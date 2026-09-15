# Remaining Work: Phases 7-12

## Phase 7 — Failure injection, security, backup, and recovery

### Work (from plan exit gate G7 / Recovery Gate)
- [ ] Inject failures: internet loss, database loss, process kill, clock skew, stale quote, duplicate event, broker rejection, partial/late fill, disk pressure, restart during open orders
- [ ] Threat-model: external data, model output, operator access, CI, dependencies, secrets, database, broker ports
- [ ] Keep broker API private/local; firewall database and management ports
- [ ] Use runtime secret injection and separate environment credentials
- [ ] Create encrypted backups and execute a clean-room restore drill
- [ ] Write runbooks for: disconnect, stale data, position mismatch, missing protection, DB failure, gateway authentication, emergency stop
- [ ] Add an external dead-man heartbeat outside the trading VM's failure domain
- [ ] Capital-safety invariants survive every mandatory failure scenario
- [ ] VM/process death triggers an external alert
- [ ] A backup has been restored and validated, not merely created
- [ ] CI has no live trade authority
- [ ] Secret scan and security review pass
- [ ] Recovery runbooks have been executed at least once

### Exit gate G7 / Recovery Gate criteria
- [ ] Capital-safety invariants survive every mandatory failure scenario
- [ ] VM/process death triggers an external alert
- [ ] A backup has been restored and validated, not merely created
- [ ] CI has no live trade authority
- [ ] Secret scan and security review pass
- [ ] Recovery runbooks have been executed at least once

---

## Phase 8 — Live-data shadow mode and operations

### Work (from plan exit gate G8 / P3 / S4)
- [ ] Connect live/delayed data as appropriate, validate bar completion, and archive incoming data
- [ ] Generate WOULD_SUBMIT intents with full risk decisions, hypothetical orders, and expected protection; never call broker submission
- [ ] Compare live features/signals with offline replay from the archived data
- [ ] Deploy on a dedicated Linux VM with no public broker port
- [ ] Add metrics for: heartbeat, data freshness, decision latency, signals, risk rejections, reconciliation state, database health, journal lag
- [ ] Send critical alerts through at least two independent channels where practical
- [ ] Run daily operational review and weekly discrepancy report

### Exit gate G8 / P3 / S4 criteria
- [ ] Zero unexplained signal differences between live archive replay and shadow decisions
- [ ] Zero stale-data decisions
- [ ] Zero duplicate hypothetical orders
- [ ] Every expected trading session has complete monitoring evidence
- [ ] External dead-man alert and recovery procedure are proven
- [ ] Strategy meets a predeclared minimum forward sample before paper promotion

---

## Phase 9 — IBKR paper integration

### Prerequisites (from plan)
- [ ] Current account/paper availability and market-data behavior verified directly
- [ ] Required subscriptions and API permissions confirmed
- [ ] Manual authentication/restart procedure documented
- [ ] Phases 1–8 applicable gates passed

### Work (from plan exit gate G9 / P4)
- [ ] Implement the IBKR adapter without leaking IBKR-specific objects into core logic
- [ ] Validate connection lifecycle, IDs, order acknowledgements, parent/child transmission, OCA behavior, rejection mapping, cancel/replace, fills, and account snapshots
- [ ] Reconcile at startup, after reconnect, after fills, on timer, and end of session
- [ ] Record known paper-simulator limitations; do not treat paper fills as live-quality evidence
- [ ] Test the manual authentication and fail-closed disconnect path
- [ ] Maintain LIVE_TRADING_ENABLED=false; use separate paper credentials/configuration

### Exit gate G9 / P4 criteria
- [ ] Zero unexplained paper positions
- [ ] Zero unresolved fills or duplicate submissions
- [ ] 100% order-state recovery after controlled restart tests
- [ ] Protective-order behavior is observed and documented
- [ ] Broker disconnect blocks new orders and alerts externally
- [ ] Local and broker state reconcile continuously

---

## Phase 10 — Extended paper validation

### Minimum criteria (from plan)
- [ ] At least 60 trading days and a predeclared meaningful number of executed signals
- [ ] More than one volatility/market condition
- [ ] Zero duplicate orders, unexplained positions, unresolved fills, hard-risk violations, or stale-data trades
- [ ] All restarts and reconnects reconcile successfully
- [ ] Realized paper behavior is compared with shadow and simulator assumptions
- [ ] Drawdowns, turnover, costs, rejects, partial fills, and sizing deviations remain within policy

### Exit gate G10 / Paper Evidence Gate criteria
- [ ] Engineering acceptance is perfect on all hard safety metrics
- [ ] Strategy evidence remains acceptable under the predeclared rules
- [ ] No critical incident remains open
- [ ] Operator runbooks and weekend/manual-authentication routine are sustainable

**No-go:** 60 days alone is insufficient if the trade sample or regime coverage is weak

---

## Phase 11 — ML ranking and optional LLM news forward test

### Stage A — ML candidate ranking
- [ ] Use only valid deterministic candidates; ML changes ranking, not hard eligibility or risk
- [ ] Define chronological labels and prevent feature leakage
- [ ] Register model ID, training period, features, hyperparameters, dataset hash, code commit, metrics, and creation date
- [ ] Compare BASELINE versus BASELINE + RANKER on identical data and forward conditions
- [ ] Reject malformed, stale, missing, or out-of-range inference
- [ ] Keep deterministic fallback behavior

### Stage B — optional LLM news features
- [ ] Treat LLM-derived historical news sentiment as unsuitable for promotion evidence unless the model and news corpus are demonstrably point-in-time
- [ ] Default policy: forward-test LLM news features only
- [ ] Convert external content to a strict schema, validate it, and store provenance
- [ ] Test prompt injection, invalid JSON, unknown symbols, contradictory output, timeout, 500 errors, NaN, and impossible confidence
- [ ] The LLM has no broker tool, credentials, network route, or order-submission capability

### Exit gate G11 / AI Gate criteria
AI qualifies only if, after costs and risk, it provides at least one durable benefit:
- [ ] higher risk-adjusted return
- [ ] similar return with lower drawdown
- [ ] similar return with fewer trades/lower costs
- [ ] improved stability or candidate precision without new safety failures

Otherwise, remove or keep it research-only. The project name is not evidence.

---

## Phase 12 — Tiny live pilot and evidence-based scaling

### Mandatory prerequisites (from plan)
- [ ] Legal/residency, tax, account, funding, and market-data obligations verified
- [ ] G0–G10 passed; G11 only if AI will be active
- [ ] Independent review of code, security, risk policy, and operational runbooks
- [ ] Explicit maximum capital, position, daily loss, drawdown, and gross-exposure limits
- [ ] Live credentials inaccessible to research, CI, and paper environments
- [ ] Operator authorization plus reconciliation OK at every startup
- [ ] Written rollback and incident plan

### Pilot rules (from plan)
- [ ] Use capital whose complete loss is tolerable
- [ ] Start below theoretical capacity and scale only after a new review window
- [ ] Do not change multiple variables at once
- [ ] AI, if used, remains removable and subordinate to deterministic risk
- [ ] Ordinary shutdown cancels/blocks as configured; liquidation is a separate explicit action

### Exit gate G12 criteria
- [ ] There is no automatic "pass into scaling." Each increase requires a new written decision based on live operational and risk evidence

---

## Summary: All Remaining Tickets (from "Immediate first backlog")

The following immediate backlog items (from the execution plan) have been verified as **complete**:
1. ✅ Create the product charter, non-goals, glossary, and risk-policy skeleton
2. ✅ Add ADRs for daily bars, cash/no leverage, common core, PostgreSQL + Parquet, and execution adapters
3. ✅ Scaffold Python project quality gates and CI without any trading logic
4. ✅ Implement domain value objects and validation
5. ✅ Implement order/position state machines and invariant tests
6. ✅ Implement environment isolation and configuration contract tests
7. ✅ Implement journal, clock, and deterministic ID interfaces
8. ⚠️ Research-data vendor spike — data infrastructure in place, vendor selection may remain bounded Phase 2 spike
9. ✅ Implement raw daily-bar and corporate-action ingestion
10. ✅ Build dataset validation and manifests
11. ✅ Implement the simulator event loop and fake broker
12. ✅ Add fills, costs, gaps, cash settlement, and restart/replay tests

**True remaining work: Phases 7 through 12** (6 phases of progressively more operational work)