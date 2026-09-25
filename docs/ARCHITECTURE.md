# Architecture

## Decision pipeline (current)

```
MARKET DATA (Parquet, point-in-time retrieval)
  -> POINT-IN-TIME DATA VALIDATION (bar validation, supersession)
  -> FEATURE ENGINE (features/indicators.py; trailing-only)
  -> REGIME ENGINE (regimes/; deterministic trend/vol/liquidity)
  -> STRATEGY CANDIDATES (strategies/; baseline MA-cross + candidates A/B/C)
  -> SIGNAL EVIDENCE + RANKING (trade_planning.SignalEvidence; score != eligibility)
  -> TRADE PLAN (trade_planning.TradePlan; risk-based sizing)
  -> STRATEGY PROMOTION GATE (promotion/; APPROVED/REJECTED/RESEARCH_ONLY artifacts)
  -> HARD RISK ENGINE (risk/risk_engine.py; final authority)
  -> approved?  NO -> REJECT + JOURNAL
  -> OMS (oms/oms.py; idempotency, OCA, ledger replay)
  -> SHADOW (shadow.py) / PAPER (broker/ibkr_paper.py via orchestration/)
  -> TRADE MANAGEMENT (ATR initial/trailing stops, time stop; research: research/backtest.py)
  -> RECONCILIATION (reconciliation/, risk.ReconciliationEngine)
  -> MONITORING / DRIFT (monitor.py, monitoring/drift.py)
  -> KILL-SWITCH CHECKS (risk/kill_switch.py)
  -> EXPERIMENT + AUDIT ARCHIVE (persistence/, artifacts/)
```

## Module map

| Module | Responsibility |
|---|---|
| `data/` | Point-in-time Parquet store, validation, ingestion, corporate actions |
| `features/` | Indicator registry + specs; LLM-output validation boundary (`features/__init__.py`) |
| `regimes/` | Deterministic regime classification (no LLM/ML) |
| `strategies/` | `ma_cross_strategy` (baseline/control) + `candidates` (families A/B/C) |
| `trade_planning/` | SignalEvidence, TradePlan, risk-based SizingPolicy |
| `validation/` | Purged splits, PSR/DSR, CSCV PBO, stationary bootstrap, White RC, concentration |
| `promotion/` | Versioned promotion policy + immutable decision artifacts |
| `research/` | Research backtester (daily bars, gap-aware), family runner, report renderer |
| `risk/` | HardRiskEngine, portfolio limits, SessionScheduler, KillSwitchCoordinator |
| `oms/` | Order lifecycle, idempotency keys, OCA groups, event ledger |
| `broker/` | BrokerAdapter protocol, fake adapter, IBKR paper adapter |
| `shadow.py` | Shadow would-submit pipeline with replay verification |
| `reconciliation/` | Position/order/fill/cash reconciliation, fail-closed incidents |
| `orchestration/` | Autonomous paper orchestrator (journal, idempotent cycles, gating) |
| `monitoring/` | Strategy drift monitor (HEALTHY/WATCH/DEGRADED/DISABLED) |
| `monitor.py` | System monitor, alert channels, data freshness |
| `persistence/` | Experiments (JSON), PostgreSQL store, shadow archive, baseline reports |
| `ml_pipeline.py`, `ml_ranking.py` | Subordinate ML ranking; can never authorize trades |
| `chaos_engine.py`, `backup.py` | Failure injection, dead-man heartbeat, encrypted backups |

## Authority hierarchy (non-negotiable)

`HardRiskEngine` > `OMS` > `Orchestrator` > `Strategy` > `ML/LLM`.

No layer may delegate upward authority that a lower layer does not already have.
Unknown state fails closed at every boundary.
