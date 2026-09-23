# Threat Model

## Assets

- Broker credentials and account identifiers
- Cash, positions, orders, fills, and protective orders
- Append-only journal and reconciliation history
- Market-data provenance and model artifacts
- Operator authorization and risk-policy versions

## Trust Boundaries

1. External market-data providers to validated ingestion
2. Untrusted AI/provider output to feature validation
3. Strategy and risk core to OMS
4. OMS to fake, shadow, or external paper adapter
5. Application to PostgreSQL and backup storage
6. Monitoring process to independent alert transports

## Threats and Controls

| Threat | Control |
|---|---|
| Lookahead or revised data | Completed-bar sequencing, UTC provenance, stale/revision rejection |
| Duplicate order or fill | Idempotency keys and unique broker/execution identifiers |
| Risk bypass | Hard-risk gate, submission kill switch, reconciliation block |
| Fake adapter mistaken for broker | Explicit adapter names and non-connecting IBKR boundary |
| LLM prompt injection or malformed output | Strict schema, bounds, provenance, and `FEATURE_REJECTED` |
| Database tampering or loss | Append-only trigger, checksums, encrypted backups, recovery tests |
| Secret leakage | Secret manager boundary, no raw AI output persistence, Gitleaks |
| Single alert failure | Independent HTTPS webhook and SMTP/TLS channels |
| Accidental live enablement | Multiple gates, expiry, independent confirmation, permanent disabled default |

## Residual Risk

Actual IBKR behavior, external alert delivery, forward shadow results, and
paper-duration evidence require authorized operational testing. This repository
does not claim those controls have been exercised against live services.
