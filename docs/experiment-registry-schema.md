# Experiment Registry Schema

**Purpose:** Register every experiment including failures before testing strategies (Phase 0 gate).

## Schema Definition

Each experiment record contains:

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Unique experiment identifier |
| `hypothesis` | String | Human-readable hypothesis description |
| `parameters` | JSON | All strategy parameters (symbols, thresholds, limits) |
| `dataset_hash` | String | SHA256 of the dataset used |
| `universe_version` | String | Version label of the point-in-time universe |
| `code_commit` | String | Git commit hash of the strategy code |
| `dependency_lock_hash` | String | Hash of dependency lock file (pyproject.lock, etc.) |
| `cost_slippage_assumptions` | JSON | Commission, slippage, spread assumptions |
| `seed` | Integer | Random seed for deterministic behavior |
| `result_metrics` | JSON | Expectancy, profit factor, Sharpe, Sortino, drawdown, turnover, exposure, trade count, win/loss distribution, MAE/MFE, concentration |
| `status` | Enum [PENDING, RUNNING, COMPLETED, REJECTED] | Experiment status |
| `created_at` | UTC datetime | When experiment was registered |
| `completed_at` | UTC datetime | When experiment finished |
| ` rejection_reason` | String | Why was the experiment rejected (if applicable) |

## Registry Interface

```python
class ExperimentRegistry:
    def register(self, hypothesis: str, parameters: dict, 
                 dataset_hash: str, universe_version: str,
                 code_commit: str, dependency_lock_hash: str,
                 cost_slippage_assumptions: dict, seed: int) -> ExperimentId:
        pass
    
    def record_result(self, experiment_id: ExperimentId, 
                      result_metrics: dict, status: str,
                      rejection_reason: str | None = None) -> None:
        pass
    
    def get_experiment(self, experiment_id: ExperimentId) -> Experiment:
        pass
    
    def list_experiments(self, status: str | None = None) -> list[Experiment]:
        pass
```

## Policy

- Every trial is registered, not only winners (S1 gate requirement).
- Failed hypotheses are marked `REJECTED` with evidence preserved.
- No tuning on the final test set — if a strategy fails, a new pre-registered hypothesis is tested.
- Code commit and dependency lock hash ensure reproducibility.
- Dataset hash and universe version prevent lookahead or post-hoc data selection.

## Exit Gate G4 / S1 Candidate Requirements

- [ ] Strategy specification has no ambiguous order or collision behavior
- [ ] Backtest and replay are deterministic
- [ ] Every trial is registered, not only winners
- [ ] Results include costs and gap behavior
- [ ] Engineering acceptance is reported separately from strategy performance