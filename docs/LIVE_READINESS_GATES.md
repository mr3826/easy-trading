# Live Readiness Gates

Live trading is **NOT AUTHORIZED**. This document records what would have to be true
*before the question could even be raised* — meeting these gates does not authorize anything;
authorization is an explicit human decision outside this codebase.

1. Point-in-time constituent universe and revised-bar history (remove survivorship ceiling).
2. A strategy family APPROVED by the promotion gate on real (not synthetic) data.
3. Sustained shadow track record with drift monitor continuously HEALTHY.
4. Sustained paper track record with reconciliation continuously clean and realized
   costs consistent with the research model (slippage/fees within modeled bounds).
5. Independent review of risk-policy versions, kill-switch thresholds, and promotion policy.
6. External controls outside code: broker-side limits, capital segregation, human
   authorization + independent confirmation (the shape enforced by
   `authorization.AuthorizationRequest`).

Gate 0 stands above all: the permanent fail-closed policy is only ever changeable by explicit
human governance, never by code or configuration.
