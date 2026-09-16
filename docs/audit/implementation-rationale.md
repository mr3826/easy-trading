# Implementation Rationale

Historical intent is recorded only where supported by the phase plan, ADRs, Git history, tests, or code behavior. Otherwise: **Intent inferred; not confirmed by repository evidence.**

| Path/group | Intended phase | Evidence and current behavior | Correctness | Decision / verification |
|---|---:|---|---|---|
| `docs/product-charter.md`, `docs/risk-policy.md`, `docs/decisions/*` | 0 | Phase plan and ADRs define daily-bar, cash, no-leverage V1 and common-core safety rules | Partial: policy exists, enforcement incomplete | Keep; link policy tests and CI |
| `trading-platform/src/trading_platform/domain` | 1 | Domain dataclasses/enums are imported by simulator, OMS, and tests | Partial: validation and immutability are incomplete | Repair; invariant/property tests |
| `data/ingestion/daily_bar_ingestion.py` | 2 | Names and validation behavior match daily-bar ingestion plan | Partial: no durable Parquet/archive integration proven | Keep/repair; PIT and stale/revision tests |
| `simulator/event_driven_simulator.py` | 3 | Event loop, fills, costs, and portfolio ledger are implemented | Correct for tested V1 paths; external market microstructure is not modeled | Keep/repair; no-lookahead, eligibility, and replay tests |
| `strategies/ma_cross_strategy.py` | 4 | Strategy module and phase tests demonstrate deterministic baseline intent | Unverified profitability; must not be presented as evidence | Keep; deterministic fixture only |
| `persistence/{experiment,baseline_report}.py` | 4/5 | Experiment registry schema doc and walk-forward code reference them | Partial; not PostgreSQL persistence | Keep/extend; hash and split tests |
| `walk_forward/walk_forward.py`, `ml_ranking.py` | 5/11 | Names, phase plan, and source behavior indicate evaluation/ranking intent | Partial/unverified | Repair; chronological and hash tests |
| `oms/oms.py`, `risk/*` | 6 | State transitions, hard controls, reconciliation, and recovery are exercised by unit/integration tests | CI-verified in-memory core; PostgreSQL durability is separate | Keep/repair; hard-risk/property/recovery tests |
| `broker_adapter.py` | 9 | Explicit `FakeBrokerAdapter`, `ShadowBrokerAdapter`, and paper-only IBKR boundary exist | Fake and boundary are verified; actual IBKR remains external | Keep/repair; fake contract tests; external IBKR remains gated |
| `chaos_engine.py` | 7 | Failure wrappers now raise/duplicate/alter dependency calls under injection | CI-verified mechanism; no production fault injection | Keep; dependency-fault tests |
| `monitor.py`, `dead_man.py` | 8 | Probes, HTTPS webhook, SMTP/TLS, and standalone heartbeat check exist | Deployment credentials and forward alert delivery remain external | Keep/repair; fake transport and dead-man tests |
| `.github/workflows/ci.yml` | 14 | Locked uv install, PostgreSQL service, static checks, coverage, Gitleaks, and artifact upload run on PRs | CI-verified; third-party Node deprecation warning remains | Keep; CI artifact proof |
| root `phase*_test.py`, `debug_*.py`, `fix_*.py` | mixed | Git history and names show exploratory repair/demo work, not production modules | Misleading as test evidence | Relocate/archive with node inventory mapping |
