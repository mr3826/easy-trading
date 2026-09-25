# IBKR Paper Operation

Adapter: `broker/ibkr_paper.py` (+ guide `ibkr-paper-guide.md`) — **IMPLEMENTED, TESTED**
(unit, fake-path); external connectivity is **REQUIRES EXTERNAL SETUP**.

## Hard boundaries

- Paper hosts/ports only — `validate_paper_target` rejects live endpoints; live accounts/ports
  are never accepted.
- Order submission requires the explicit paper-submission flag AND a hard-risk-approved order.
  Connect-only smoke test: `tests/external/test_ibkr_paper_smoke.py` (`pytest -m external`).
- This environment currently has **no IBKR configuration**, so the external smoke test is
  `REQUIRES_EXTERNAL_SETUP` (not faked). Operator steps when ready:

```
# TWS/Gateway PAPER session, API enabled, paper port (e.g. 7497)
set IBKR_PAPER connect env vars per docs/ibkr-paper-guide.md
uv run pytest tests/external/test_ibkr_paper_smoke.py -m external -v
```

## Session discipline

Startup -> paper session validation -> targeted/periodic reconciliation
(`SessionScheduler`) -> reconnect handling with broker snapshot sync. Unhealthy
reconciliation blocks submissions.
