# Risk Policy — V1 Skeleton (All Values PROVISIONAL)

**Date:** 2026-09-02  
**Owner:** Evan  
**Version:** 0.1-PROVISIONAL  

> **Do not hard-code final numbers in Phase 0.** Each value needs evidence and a test showing it can trigger. Every limit must have: rationale, unit and calculation, scope and reset behavior, reachable boundary test, reject/halt action, alert severity, and policy version recorded with each decision.

## Position & Exposure Limits

| Limit | Value | Rationale | Unit | Scope | Reset Behavior |
|---|---|---|---|---|---|
| Maximum position notional | PROVISIONAL | To be determined by position-sizing tests | currency | Per instrument | Reset at session start |
| Maximum gross exposure | PROVISIONAL | To be determined by portfolio stress tests | currency | Portfolio-wide | Reset at session start |
| Maximum correlated/beta-weighted exposure | PROVISIONAL | To be determined by correlation stress tests | currency | Portfolio-wide | Reset at session start |
| Daily loss limit | PROVISIONAL | To be determined by drawdown distribution tests | currency | Daily P&L | Resets at midnight UTC |
| Peak-to-trough drawdown halt | PROVISIONAL | To be determined by Monte Carlo simulations | currency | Portfolio trough to peak | Resets on new peak |
| Maximum sizing error after whole-share quantization | PROVISIONAL | To be determined by quantization error tests | shares | Per order | N/A |

## Risk Controls

| Control | Value | Rationale | Unit | Trigger | Action | Severity |
|---|---|---|---|---|---|---|
| Minimum liquidity threshold | PROVISIONAL | To be determined by spread/distribution analysis | price (%) | Order submission | Reject order | High |
| Maximum spread | PROVISIONAL | To be determined by market microstructure analysis | price (%) | Order submission | Reject order | High |
| Earnings blackout window | PROVISIONAL | To be determined by corporate action calendar | days | Around earnings date | Block entry | Medium |
| Maximum data age for decisions | PROVISIONAL | To be determined by latency tests | hours | Decision timestamp | Block decision | High |
| Maximum reconciliation mismatch duration | PROVISIONAL | To be determined by fail-closed tests | hours | Since last reconcile | Block submissions | Critical |

## Policy Versioning

Each decision records the policy version that was in effect. Version changes require:
1. Rationale documented
2. Boundary tests passed
3. Alert severity confirmed
4. Review by operator before promotion

## Exit Gate G0 Reference

- [ ] Risk policy skeleton created with all values marked PROVISIONAL
- [ ] Boundary tests designed for each PROVISIONAL limit
- [ ] No live trading enabled until policy values are validated