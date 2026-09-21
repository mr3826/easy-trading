# Founder Handoff Queue

Consolidated by the orchestrator only. Workers must not contact or block on the
founder independently. Each item records: what is missing, what engineering did
instead, and the exact founder action required. No item on this list blocks
independent engineering work.

## Queue

| # | Item | Status | Engineering substitute in place | Founder action required |
|---|---|---|---|---|
| 1 | Market-data vendor selection and licensing (contract, terms acceptance, possible payment) | OPEN | Vendor-neutral `MarketDataProvider` interface, deterministic fake provider, local Parquet fixtures; `docs/data-vendor-decision-matrix.md` criteria ready | Run the vendor spike, accept terms, fund the dataset; supply canonical point-in-time dataset |
| 2 | IBKR paper account and approved TWS/Gateway connection window | OPEN | `IBKRPaperBrokerAdapter` boundary with two-flag opt-in (`--allow-connection`, `--allow-paper-orders`), paper-only external smoke test (skipped by default), live port/account rejection | Provision a verified paper account, approve a test window, provide connection details via secret manager |
| 3 | Alert transport credentials (SMTP sender/recipient, HTTPS webhook endpoint) | OPEN | `EmailAlertChannel`/`WebhookAlertChannel` interfaces with fake adapters for tests; password via callback, never stored; `docs/alert-setup.md` checklist | Create endpoints, store secrets in deployment secret manager, send test alerts |
| 4 | Deployment environment (host, secret manager, network paths for the trading process and dead-man monitor) | OPEN | Dead-man CLI is standalone and deployment-agnostic; configuration templates without real values | Choose and provision deployment; no engineering action possible without it |
| 5 | Dead-man external deployment (independent process/transport that pages a human) | OPEN | In-process heartbeat + standalone CLI; external paging configuration documented | Deploy an external paging transport and confirm delivery |
| 6 | Live trading authorization | PERMANENTLY CLOSED BY POLICY | `LIVE_TRADING_ENABLED=false`, `LIVE_STATUS=NOT_AUTHORIZED`; `authorize_live()` always returns disapproved; config forcibly neutralizes enabling flags | Requires a separate human authorization process; no code change can enable it |
| 7 | Legal/account checkpoint (residency, KYC, tax-reporting obligations) | OPEN | Documented in `docs/product-charter.md` as an explicit checkpoint | Confirm legal/tax facts before any external account step |
| 8 | Risk-policy value promotion (all values PROVISIONAL) | OPEN | Boundary tests exercise every limit; promotion requires operator review per `docs/risk-policy.md` | Review evidence and approve promoted values |
| 9 | Encrypted backup restore drill on real infrastructure | OPEN | Encrypted backup + checksum verification implemented and tested; clean-room restore procedure documented without claiming the drill | Schedule and observe the real restore drill |
| 10 | Shadow forward run (60-day paper/shadow observation) | OPEN | Shadow orchestrator with archival/replay comparison implemented; no forward evidence claimed | Authorize and observe forward operation |

## Rules

1. Items 1-5 and 7-10 remain engineering-independent: work continues with
   interfaces, fakes, and explicitly marked safe test configuration.
2. Only the orchestrator updates this file.
3. A founder answer for one item is not authorization for any other item.
