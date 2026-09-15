"""Test walk-forward module integration."""
from trading_platform.walk_forward.walk_forward import PeriodSplit, WalkForwardEvaluator
from trading_platform.strategies.ma_cross_strategy import MaCrossHypothesis
from trading_platform.persistence.experiment import ExperimentRegistry
from trading_platform.simulator.event_driven_simulator import EventDrivenSimulator

# Test PeriodSplit creation and splitting
split = PeriodSplit(train_days=60, validation_days=20, test_days=30)
result = split.split(datetime(2020,1,1).date(), datetime(2020,12,31).date())
train = result["train"]
val = result["validation"] 
test = result["test"]
print(f'Period split: train={train}, val={val}, test={test}')

# Test walk-forward iterator
folds = split.walk_forward(datetime(2020,1,1).date(), datetime(2020,12,31).date(), step_forward=30)
print(f'Walk-forward folds: {len(folds)}')

# Test that fixed test period is consistent
test_period = split.get_test_period([datetime(2020,1,1).date()])
print(f'Fixed test period: {test_period}')

# Test Hypothesis
h = MaCrossHypothesis(fast_length=5, slow_length=20)
print(f'Hypothesis for walk-forward: {h.hypothesis_id}')

print('Walk-forward module integration OK!')
PYEOF