# Risk Register — integration/remaining-platform-work

Base: verified PR head `37ebec9`. Statuses: OPEN (work required), MITIGATED
(engineering control in place), EXTERNAL (founder/operator gate), CLOSED
(verified). Severity: high / critical / medium.

| # | Risk | Severity | Status | Mitigation / control |
|---|---|---|---|---|
| 1 | Lookahead: walk-forward signal generation uses future bars (SMA window anchored at period end) | critical | OPEN | Agent B: as-of signal generation; orchestrator: MARKET fills at next open; end-to-end no-lookahead tests (R7, R21) |
| 2 | Fake reconciliation theater: placeholder commissions, hardcoded startup cash, stub fill reconciliation | critical | OPEN | Agent C: derive from durable ledger events; real fill/startup/reconnect reconciliation (R10) |
| 3 | Reconciliation compares copied OMS state against itself | critical | OPEN | Agent C + E: independent broker snapshots; tests must not compare duplicated copies (R11, C2) |
| 4 | Fail-closed chain unwired: mismatch does not block orders, no incident, no alert | critical | OPEN | Agent C: wire BLOCK_NEW_ORDERS → PERSIST_INCIDENT → CRITICAL_ALERT → OPERATOR_RESOLUTION_REQUIRED (R12) |
| 5 | Idempotency hole: duplicate submission while SUBMITTED silently re-accepted; event-ledger wipe on cancel-all | high | OPEN | Agent C: fix hole + append-only semantics; rewrite hole-encoding test (R8, R9) |
| 6 | Shadow mode is a sink stub; monitor claims replay comparison with no implementation | high | OPEN | Agent D: genuine shadow orchestrator with archival/replay (R14) |
| 7 | Zero property-based tests; hard-risk limits example-tested only | high | OPEN | Agent A: domain property tests; Agent C: Hypothesis hard-risk tests (R1, R13) |
| 8 | Placeholder experiment hashes and baseline report fields | high | OPEN | Agent B: real hashes, rejected malformed records, populated report fields (R5, R6) |
| 9 | Market data dead-end: get_bars returns [], ingestion unreachable from src | high | OPEN | Agent A: real Parquet pipeline, vendor-neutral interface, deterministic fixtures (R2-R4) |
| 10 | Test integrity: hardcoded absolute sys.path imports root checkout; tautological safety tests | high | OPEN | Agent C: portable imports; Agents A/B/C: meaningful assertions (R20) |
| 11 | IBKR adapter without real client; single-flag gate | high | OPEN | Agent E: pinned library, two-flag gate, live rejection, opt-in external harness (R17) |
| 12 | Monitoring checks caller-fed; no disk check; alert defaults not independent | high | OPEN | Agent D: real probes + injectable dependencies + independent transports (R15, R16) |
| 13 | ML promotion blocking is data-only; LLM timeouts missing; staleness partial | high | OPEN | Agent F: promotion enforcement, timeout handling, staleness checks (R18, R19) |
| 14 | Accidental live enablement | critical | MITIGATED | `authorize_live()` always disapproved; config neutralizes enabling flags; tested (verified in audit) |
| 15 | Journal tampering or loss | critical | MITIGATED | Append-only DB trigger, checksums, AES-GCM encrypted backups, fail-closed recovery (verified in audit) |
| 16 | Duplicate order or fill at DB level | high | MITIGATED | Unique broker_order_id/broker_trade_id, idempotency_key, exactly-once fills (verified in audit) |
| 17 | Secret leakage | high | MITIGATED | No credentials in source; password via callback; Gitleaks + secret_scan pass (verified) |
| 18 | Single alert failure | medium | EXTERNAL | Independent HTTPS webhook + SMTP/TLS designed; real delivery requires founder credentials (queue #3) |
| 19 | External gates claimed complete through code alone | critical | MITIGATED | Evidence bundle separates code/CI proof from external/forward gates; status ceilings honest |
| 20 | Vendor lock-in or licensing breach | high | EXTERNAL | Vendor-neutral interface; no vendor selected; founder runs spike (queue #1) |
| 21 | Simulator→OMS→persistence chain unconnected | high | OPEN | Orchestrator integration: contracts C1-C6 connect simulator, OMS, shadow, reconciliation (R21) |
| 22 | POSIX tmp path (`/tmp/trading_experiments`) on Windows | medium | OPEN | Agent B: platform-appropriate experiments root |
| 23 | Duplicate type definitions (CorporateAction ×2, SystemMonitor ×2) | medium | OPEN | Agent A: single CorporateAction; Agent D + C: monitor consolidation at integration |
| 24 | Coverage regression during integration | medium | OPEN | Orchestrator: full suite + coverage gates after every integration (80/90 thresholds) |
| 25 | Dependency drift / unvetted additions | medium | MITIGATED | Orchestrator-only dependency changes; uv.lock committed with pyproject; pip-audit gate |
