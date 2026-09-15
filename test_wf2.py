"""Test walk-forward module integration - direct import."""
import sys
import importlib

# Force fresh import by clearing any cached modules
for mod in list(sys.modules.keys()):
    if 'trading_platform' in mod:
        del sys.modules[mod]

# Import walk_forward module directly
sys.path.insert(0, r'D:\hexabyte_technologies\easy-trading\trading-platform\src')

# Import the module by loading its source file
import importlib.util
spec = importlib.util.spec_from_file_location(
    "walk_forward_module",
    r"D:\hexabyte_technologies\easy-trading\trading-platform\src\trading_platform\walk_forward\walk_forward.py"
)
wf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wf)

PeriodSplit = wf.PeriodSplit
WalkForwardEvaluator = wf.WalkForwardEvaluator

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

# Test Hypothesis from strategies module
sys.path.insert(0, r'D:\hexabyte_technologies\easy-trading\trading-platform\src')
from trading_platform.strategies.ma_cross_strategy import MaCrossHypothesis

h = MaCrossHypothesis(fast_length=5, slow_length=20)
print(f'Hypothesis for walk-forward: {h.hypothesis_id}')

print('Walk-forward module integration OK!')
PYEOF