# Test Node Reconciliation

## Evidence

- Baseline source inventory: `artifacts/verification/test-inventory-before.txt`, generated from `git grep` at starting commit `9b461b5`; 47 exact function nodes.
- Intermediate explicit inventory: `5353cc1:artifacts/verification/test-inventory-after.txt`; 34 nodes.
- Current explicit inventory: `artifacts/verification/test-inventory-current.txt` and `test-inventory-after.txt`; 79 collected nodes.
- Current collection command: `uv run pytest --collect-only`; exit 0.

The apparent 47→34 decrease was a net count, not deletion of 13 valid tests. The 47 baseline consisted of 24 configured unit tests plus 23 root-level scripts discovered accidentally by recursive fallback. The intermediate 34 consisted of the 24 configured tests plus 10 new safety/migration tests. The 23 root nodes were relocated afterward, and additional safety tests were added; current explicit collection is 79.

## Every intermediate missing node

| Old node ID | Why absent from intermediate 34 | Replacement/current node |
|---|---|---|
| `test_phase4.py::test_hypothesis` | Root script excluded when `testpaths` became explicit | `trading-platform/tests/integration/test_phase4.py::test_hypothesis` |
| `test_phase4.py::test_position_limit` | Same | `.../test_phase4.py::test_position_limit` |
| `test_phase4.py::test_buying_power` | Same | `.../test_phase4.py::test_buying_power` |
| `test_phase4.py::test_experiment_record` | Same | `.../test_phase4.py::test_experiment_record` |
| `test_phase4.py::test_metrics` | Same | `.../test_phase4.py::test_metrics` |
| `test_phase4.py::test_concentration` | Same | `.../test_phase4.py::test_concentration` |
| `phase6_integration_test.py::test_oms_state_machine_lifecycle` | Root integration script excluded | `trading-platform/tests/integration/test_phase6_integration.py::test_oms_state_machine_lifecycle` |
| `phase6_integration_test.py::test_oms_idempotency` | Same | `.../test_phase6_integration.py::test_oms_idempotency` |
| `phase6_integration_test.py::test_oca_group` | Same | `.../test_phase6_integration.py::test_oca_group` |
| `phase6_integration_test.py::test_hard_risk_engine` | Same | `.../test_phase6_integration.py::test_hard_risk_engine` |
| `phase6_integration_test.py::test_reconciliation_engine` | Same | `.../test_phase6_integration.py::test_reconciliation_engine` |
| `phase6_integration_test.py::test_session_scheduler` | Same | `.../test_phase6_integration.py::test_session_scheduler` |
| `phase6_integration_test.py::test_broker_contract` | Same | `.../test_phase6_integration.py::test_broker_contract` |
| `test_wf.py::test_period_split_creation` | Root script excluded | `trading-platform/tests/integration/test_walk_forward_periods.py::test_period_split_creation` |
| `test_wf.py::test_walk_forward_folds` | Same | `.../test_walk_forward_periods.py::test_walk_forward_folds` |
| `test_wf.py::test_get_test_period` | Same | `.../test_walk_forward_periods.py::test_get_test_period` |
| `test_wf.py::test_hypothesis` | Same | `.../test_walk_forward_periods.py::test_hypothesis` |
| `test_wf.py::test_dataset_hash` | Same | `.../test_walk_forward_periods.py::test_dataset_hash` |
| `test_wf2.py::test_period_split_creation` | Duplicate root script excluded | `trading-platform/tests/integration/test_walk_forward_periods_retried.py::test_period_split_creation` |
| `test_wf2.py::test_walk_forward_folds` | Same duplicate | `.../test_walk_forward_periods_retried.py::test_walk_forward_folds` |
| `test_wf2.py::test_get_test_period` | Same duplicate | `.../test_walk_forward_periods_retried.py::test_get_test_period` |
| `test_wf2.py::test_hypothesis` | Same duplicate | `.../test_walk_forward_periods_retried.py::test_hypothesis` |
| `test_wf2.py::test_dataset_hash` | Same duplicate | `.../test_walk_forward_periods_retried.py::test_dataset_hash` |

No valid baseline node remains missing from the current explicit inventory. The two walk-forward files are preserved as separate nodes because the baseline had both; they should be merged in a future cleanup only with an explicit node-removal decision.
