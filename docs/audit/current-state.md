# Current Platform State

Updated after final local verification at source SHA
`7f7cab653d1c791aba426a68327e3bbdb577d741`.

## Code and CI Evidence

- Integration branch: `integration/remaining-platform-work`.
- Local PostgreSQL 16 integration: 2 passed.
- Non-external suite: 228 passed; 2 external tests deselected.
- Overall coverage: 89%.
- Critical coverage: 93% aggregate (`oms 95%`, `risk_engine 91%`,
  `reconciliation 100%`, `authorization 93%`).
- Ruff, formatting, strict mypy, import smoke, dependency audit, and secret
  scan passed.
- External IBKR tests were collected but not executed.

## Implemented Code

- Daily-bar Parquet read/write, raw archival, checksums, revisions, corporate
  action boundaries, and point-in-time availability filtering.
- Chronological research, real experiment provenance hashes, walk-forward
  no-lookahead checks, robustness analyses, and deterministic replay.
- OMS ledger protections, policy-versioned risk approval, independent broker
  reconciliation, incident blocking, partial-fill validation, and recovery
  replay.
- Genuine shadow orchestration, archival/replay, monitoring probes, chaos
  injection, encrypted backup/restore, and no submission method.
- IBKR paper adapter boundary with lazy client import, paper target checks,
  global live kill switch, two boolean authorization flags, callbacks, and
  external smoke tests.
- Deterministic ML baseline/trainable ranker, provenance hashes, promotion
  gate, and fail-closed LLM feature validation.

## Open or External Gates

- Protective-order enforcement remains policy-configurable; production must
  enable the protective-order requirement and verify active same-symbol stop
  coverage before any live or paper execution.
- Real IBKR paper connectivity and any paper order submission were not done.
- Vendor licensing/selection, external alert delivery, dead-man deployment,
  60-day paper/shadow observation, and production restore drill remain open.
- No profitability, forward-performance, or live-trading claim is made.

## Permanent Restriction

```text
LIVE_TRADING_ENABLED=false
LIVE_STATUS=NOT_AUTHORIZED
```
