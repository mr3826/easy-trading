# Current State Audit — easy-trading

Audit date: 2026-09-23
Audited HEAD: `c333219476555930578b1643791f56befb8f7c30` (branch `main`, fast-forwarded from `origin/main`)
Working tree at audit start: clean on `main` (pre-existing debug-script modifications on
`fix/verified-platform-foundation` were stashed, not destroyed).

## Verified to exist (code inspected, tests passing)

| Area | Location | State |
|---|---|---|
| Daily US equity bars, Parquet | `data/`, `data/ingestion/` | IMPLEMENTED, TESTED |
| Point-in-time retrieval w/ superseded versions | `data/__init__.py` (`ParquetMarketDataProvider`, `get_superseded`) | IMPLEMENTED, TESTED |
| Deterministic simulator | `simulator/event_driven_simulator.py` | IMPLEMENTED, TESTED |
| 5/20 SMA baseline strategy | `strategies/ma_cross_strategy.py` | IMPLEMENTED, TESTED — baseline only |
| Hard risk engine + policy versions | `risk/risk_engine.py`, `risk/limits.py` | IMPLEMENTED, TESTED |
| OMS (idempotency, OCA, ledger replay) | `oms/oms.py` | IMPLEMENTED, TESTED |
| Reconciliation + fail-closed incident chain | `reconciliation/`, `risk/risk_engine.py` | IMPLEMENTED, TESTED |
| Shadow execution | `shadow.py` | IMPLEMENTED, TESTED |
| IBKR paper adapter (gated) | `broker/ibkr_paper.py` | IMPLEMENTED, TESTED locally; live-reachable paths REQUIRES EXTERNAL SETUP |
| Experiment persistence (JSON, provenance hashes) | `persistence/experiment.py` | IMPLEMENTED, TESTED |
| Walk-forward evaluation | `walk_forward/walk_forward.py` | IMPLEMENTED, TESTED — no purging/embargo |
| ML ranking + promotion gate (ML subordinate) | `ml_ranking.py`, `ml_pipeline.py` | IMPLEMENTED, TESTED — ranker only, no execution authority |
| Monitoring/alerting/heartbeat | `monitor.py`, `dead_man.py`, `chaos_engine.py` | IMPLEMENTED, TESTED |
| PostgreSQL persistence + migrations | `persistence/postgres.py`, `migrations/0001..0002` | IMPLEMENTED, TESTED (CI service) |
| Live-trading prohibition (fail-closed) | `authorization.py`, `config.py` | IMPLEMENTED, TESTED — `authorize_live` always rejects |
| CI (ruff, format, mypy, pytest, coverage ≥80%, critical ≥90%, pip-audit, gitleaks) | `.github/workflows/ci.yml` | CI-VERIFIED at HEAD (228 tests green per `artifacts/verification/test-results.txt`) |

## Partial / gaps relevant to alpha validation

| Gap | Notes |
|---|---|
| Feature engine | No point-in-time technical indicator library exists. `features/` only contains LLM news-output validation. SMA computed inline in the strategy only. |
| Market regime engine | Absent. No trend/volatility/liquidity classification. |
| Strategy families | Only the MA-cross baseline. No trend+RS, breakout+volume, or pullback hypotheses. |
| Signal evidence structure | Strategies return raw dicts; no `SignalEvidence` with provenance/score/rejection reasons. |
| TradePlan object | Absent; signal → order intent is direct. |
| Position sizing | Shares are fixed at 1; affordability is enforced by the simulator. No risk-based sizing. |
| Exit management | Opposite-signal + max-holding only. No ATR stop / trailing / time-stop variants. |
| Reverification statistics | Walk-forward + bootstrap drawdown + parameter/cost sensitivity exist. Missing: purging/embargo, Deflated Sharpe, Probabilistic Sharpe, PBO/CSCV, multiple-testing trial counting across families, regime decomposition, concentration analysis. |
| Promotion gate for strategies | ML promotion gate exists; no strategy-level promotion artifact gating paper execution. |
| Autonomous paper orchestrator | Not present as a single service; shadow orchestrator is the closest component. |
| Strategy drift monitor | Absent. |
| Survivorship audit | Not documented; universe is an engineering universe — point-in-time constituent data does not exist → evidence ceiling applies. |

## Requires external setup

- IBKR paper connect-only smoke test (`tests/external/test_ibkr_paper_smoke.py`, marked `external`).
- PostgreSQL integration tests (`-m postgres`) need a live database (CI provides it).
- Telegram/email/webhook alerting need credentials.

## Known evidence ceilings

- Universe bias: engineering universe, not point-in-time index constituents. Backtests carry
  a survivorship-bias ceiling until point-in-time constituent membership exists.
- Intraday behavior is modeled from daily bars only (ADR-001); gap/stop fills are modeled, not observed.
- Simulator costs are modeled (fixed commission + slippage); real spread/queue effects unknown.
- No forward/paper track record exists for any strategy yet.

## Safety posture

- `LIVE_TRADING_ENABLED=false`, `LIVE_STATUS=NOT_AUTHORIZED`; `config.load_config` force-resets any
  attempted enablement; `authorize_live` rejects unconditionally as final gate.
- No code path allows strategy/ML to bypass `HardRiskEngine`.
