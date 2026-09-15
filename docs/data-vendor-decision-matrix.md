# Data-Vendor Decision Matrix

**Date:** 2026-09-02  
**Owner:** Evan  
**Status:** Spike required before vendor selection

## Criteria

| Criterion | Weight | Notes |
|---|---|---|
| Historical data coverage (US stocks) | High | Must cover target universe with splits, dividends, delistings |
| Point-in-time availability | High | Data must be time-partitioned; no lookahead adjustments |
| Corporate action fidelity | High | Splits, dividends, ticker changes must be accurately modeled |
| Delisting handling | High | Must handle delisted symbols correctly in history |
| License terms | Medium | Permit versioned historical access for research |
| Pricing model | Medium | Adjusted close vs close-only affects backtest results |
| Schema stability | Low | API/schema changes should not break ingestion |
| Refresh frequency | Low | Daily update sufficient for V1 |
| Cost | Medium | Must fit within cost ceiling |
| Reproducibility | High | Re-running ingestion against same snapshot yields identical hashes |

## Candidate Vendors (to be evaluated after spike)

| Vendor | Coverage | Point-in-time | Corporate Actions | Cost | Notes |
|---|---|---|---|---|---|
| Vendor A | TBD | TBD | TBD | TBD | To be evaluated |
| Vendor B | TBD | TBD | TBD | TBD | To be evaluated |
| IBKR Historical Data | Partial | Yes | Limited | Included with paper | Good for spike, not canonical |

## Decision Matrix

| Outcome | Action |
|---|---|
| Vendor meets all High-weight criteria | Proceed with vendor contract |
| Vendor meets High criteria but fails Medium | Negotiate terms or select alternative |
| Vendor fails High criteria | Reject; select alternative or conduct further spike |
| No vendor meets criteria | Define minimal acceptable subset; adjust V1 scope |

## Frozen Dataset Manifest

After vendor selection, a frozen dataset manifest will be created containing:
- Dataset hash (SHA256) of raw source
- Universe version (point-in-time symbol list)
- Data version and schema version
- Checksums per symbol/file
- Corporate action event log
- Licensing attribution