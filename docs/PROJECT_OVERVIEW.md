# Project Overview

**easy-trading** is a trading research + paper-execution platform for US equities.

- **Horizon/universe:** daily completed bars, long-only, cash account (no leverage), max 3 positions (V1 policy).
- **Purpose:** discover whether a *repeatable edge exists* after costs, bias controls,
  multiple-testing correction, regime changes, and genuinely unseen data — not to make a
  backtest look profitable.
- **Execution target:** research, simulation, shadow, and **IBKR paper** only.
  **Live trading is permanently disabled** (`LIVE_TRADING_ENABLED=false`,
  `LIVE_STATUS=NOT_AUTHORIZED`; `trading_platform/authorization.py` rejects unconditionally).

## Reading map

| Topic | Document |
|---|---|
| Current verified state | [current-state-audit.md](current-state-audit.md), [audit/current-state.md](audit/current-state.md) |
| Architecture | [ARCHITECTURE.md](ARCHITECTURE.md), [architecture-overview.md](architecture-overview.md) |
| Research method | [STRATEGY_RESEARCH_METHOD.md](STRATEGY_RESEARCH_METHOD.md), [research-methodology.md](research-methodology.md) |
| Validation & overfitting | [BACKTEST_VALIDATION.md](BACKTEST_VALIDATION.md), [OVERFITTING_PROTECTION.md](OVERFITTING_PROTECTION.md) |
| Promotion | [STRATEGY_PROMOTION_GATE.md](STRATEGY_PROMOTION_GATE.md) |
| Execution | [ORDER_EXECUTION.md](ORDER_EXECUTION.md), [IBKR_PAPER_OPERATION.md](IBKR_PAPER_OPERATION.md), [SHADOW_OPERATION.md](SHADOW_OPERATION.md) |
| Risk & safety | [RISK_MANAGEMENT.md](RISK_MANAGEMENT.md), [KILL_SWITCHES.md](KILL_SWITCHES.md), [SECURITY_MODEL.md](SECURITY_MODEL.md) |
| Operations | [OPERATIONS_RUNBOOK.md](OPERATIONS_RUNBOOK.md), [FAILURE_MODES.md](FAILURE_MODES.md), [DISASTER_RECOVERY.md](DISASTER_RECOVERY.md) |
| Testing | [TESTING.md](TESTING.md) |
| Honest limits | [LIMITATIONS.md](LIMITATIONS.md) |

## Status vocabulary used across docs

- **IMPLEMENTED** — code exists and runs.
- **TESTED** — covered by automated tests run in CI.
- **CI-VERIFIED** — passing quality gates at HEAD.
- **SIMULATED** — behavior demonstrated on synthetic/modeled data only.
- **REQUIRES EXTERNAL SETUP** — needs operator-provided credentials/data/services.
- **REQUIRES FORWARD EVIDENCE** — cannot be claimed until shadow/paper track record exists.
- **NOT AUTHORIZED** — explicitly prohibited by platform policy.
