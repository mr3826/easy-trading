# Worktree Ownership Classification

**Recorded:** 2026-09-16, continuation session.
**Backup patches:** `artifacts/verification/p0-full-diff.patch` (full binary-capable diff), `p0-git-status.txt`, `p0-diff-names.txt`, `p0-diff-stat.txt`, `p0-untracked.txt`, `p0-staged.patch`.

## Classification basis

Baseline `git status` was captured before this continuation. Files that were clean at baseline and changed during this session are agent-owned. Files already modified before this session and also touched by this session are **mixed**.

## Clear ownership — created or exclusively modified in this session

- `pyproject.toml` (tooling repair)
- `.github/workflows/ci.yml` (CI rewrite; the prior version was committed at 9b461b5)
- `walk_forward_first_fold.py` (deletion; file was clean at baseline)
- `scripts/` (import_smoke.py, walk_forward_first_fold.py, generate_verification.py)
- `docs/audit/*`, `docs/plans/remaining-execution-plan.md`
- `artifacts/verification/*`
- `trading-platform/migrations/0001_core.sql`
- `trading-platform/src/trading_platform/authorization.py`, `backup.py`, `config.py`, `shadow.py`
- `trading-platform/src/trading_platform/cli/`, `features/`, `observability/`, `reconciliation/`
- `trading-platform/tests/unit/test_safety_boundaries.py`, `test_migrations_and_imports.py`

## MIXED — blocker-tagged: pre-existing user/agent changes interleaved with this session

These files were already modified (uncommitted) at session start and were subsequently edited here. They cannot be separated without reconstructing two histories from the backup patch. They are committed in a dedicated commit whose message names the mixed authorship, with the pre-conflict state preserved in `p0-full-diff.patch`:

- `trading-platform/src/trading_platform/` : `__init__.py`, `broker_adapter.py`, `chaos_engine.py`, `ml_ranking.py`, `monitor.py`, `data/__init__.py`, `data/ingestion/__init__.py`, `data/ingestion/daily_bar_ingestion.py`, `domain/__init__.py`, `oms/oms.py`, `persistence/baseline_report.py`, `persistence/experiment.py`, `risk/__init__.py`, `risk/limits.py`, `risk/risk_engine.py`, `simulator/event_driven_simulator.py`, `strategies/ma_cross_strategy.py`, `walk_forward/walk_forward.py`
- `trading-platform/tests/unit/domain/test_domain.py`, `trading-platform/tests/unit/simulator/test_simulator.py`
- `uv.lock`, `docs/experiment-registry-schema.md`

## Mixed debris (not production; pre-existing scratch formatting touched by repo-wide ruff format)

- root: `debug_*.py`, `phase*_test.py`, `phase*_demo.py`, `phase6_integration_test.py`, `temp_check.py`, `test_phase4.py`, `test_wf.py`, `test_wf2.py`
- untracked scratch: `analyze_*.py`, `fix_*.py`, `show_*.py`, `count_errors.py`, `check_ruff.py`, `extract_errors.py`, `mypy_*.txt`, `ruff_*.txt`

Decision: tracked debris committed in its pre-final state as-is (no content removal); untracked scratch left untracked and excluded from lint collection.
