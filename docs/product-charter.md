# Product Charter

**Version:** 1.0  
**Date:** 2026-09-02  
**Owner:** Evan  

## V1 Scope

| Area | Decision |
|---|---|
| Market | US-listed common stocks only |
| Timeframe | Daily bars |
| Direction | Long-only |
| Account model | Cash, no leverage |
| Position count | Maximum 3 |
| Holding period | Days to weeks |
| Extended hours | Disabled |
| Earnings | No new entry or holding through scheduled earnings initially |
| Execution modes | Simulator → shadow → IBKR paper → optional tiny live pilot |
| Broker candidate | Interactive Brokers through TWS API / IB Gateway |
| Research data | Dedicated, versioned historical dataset; IBKR is not the canonical research archive |
| Runtime | One Python application plus PostgreSQL and Parquet |
| V1 exclusions | Redis, Kafka, Kubernetes, microservices, n8n in the trading path, LLM order decisions, public broker ports |

## Explicit Non-Goals

- No high-frequency or intraday trading in V1.
- No shorting, options, leverage, crypto, or futures.
- No self-modifying strategy or autonomous model promotion.
- No LLM access to broker credentials or order-submission functions.
- No production dashboard until the CLI, journal, OMS, recovery, and alerts are proven.
- No claim of historical validity from a frozen present-day symbol list.
- No live deployment merely because the software runs successfully.

## Legal/Account Checkpoint

- Actual legal and tax residence
- Permitted broker account and funding route
- Market-data licensing and display restrictions
- Reporting and tax-record obligations

## Data Vendor Spike Requirements

- Coverage of target universe including corporate actions, delistings
- Point-in-time availability of data
- Licensing terms allowing versioned historical access
- Reproducibility: re-running ingestion against same source snapshot yields same hashes
- Split/dividend adjustment fidelity verification

## Risk Policy Skeleton

All values marked `PROVISIONAL` — subject to testing and evidence before promotion.

- Maximum position notional: PROVISIONAL (to be determined by testing)
- Maximum gross exposure: PROVISIONAL
- Maximum correlated/beta-weighted exposure: PROVISIONAL
- Daily loss limit: PROVISIONAL
- Peak-to-trough drawdown halt: PROVISIONAL
- Maximum sizing error after whole-share quantization: PROVISIONAL
- Minimum liquidity threshold: PROVISIONAL
- Maximum spread: PROVISIONAL
- Earnings blackout window: PROVISIONAL
- Maximum data age for decisions: PROVISIONAL
- Maximum reconciliation mismatch duration: PROVISIONAL

## Exit Gate G0 Checklist

- [ ] No unresolved contradiction in V1 account, timeframe, or execution mode
- [ ] Live and paper credentials are explicitly out of scope for early phases
- [ ] Engineering can proceed without misrepresenting residency or KYC facts
- [ ] Data requirements are documented; vendor selection may remain a bounded Phase 2 spike