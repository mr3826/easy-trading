"""Phase 6 walk-forward module tests."""
from datetime import datetime
from trading_platform.walk_forward.walk_forward import PeriodSplit, WalkForwardEvaluator
from trading_platform.strategies.ma_cross_strategy import MaCrossHypothesis
from trading_platform.persistence.experiment import ExperimentRegistry, ExperimentRecord


def test_period_split_creation():
    """Test PeriodSplit creation and splitting."""
    split = PeriodSplit(train_days=60, validation_days=20, test_days=30)
    result = split.split(datetime(2020, 1, 1).date(), datetime(2020, 12, 31).date())
    train = result["train"]
    val = result["validation"]
    test = result["test"]
    assert train is not None
    assert val is not None
    assert test is not None


def test_walk_forward_folds():
    """Test walk-forward iterator."""
    split = PeriodSplit(train_days=60, validation_days=20, test_days=30)
    folds = split.walk_forward(datetime(2020, 1, 1).date(), datetime(2020, 12, 31).date(), step_forward=30)
    assert len(folds) > 0


def test_get_test_period():
    """Test that fixed test period is consistent."""
    split = PeriodSplit(train_days=60, validation_days=20, test_days=30)
    # Use a larger date range to avoid "data too short" error
    test_period = split.get_test_period([datetime(2020, 1, 1).date(), datetime(2025, 12, 31).date()])
    assert test_period is not None


def test_hypothesis():
    """Test Hypothesis for walk-forward."""
    h = MaCrossHypothesis(fast_length=5, slow_length=20)
    assert h.hypothesis_id is not None
    assert h.fast_length == 5
    assert h.slow_length == 20


def test_dataset_hash():
    """Test compute_dataset_hash static method."""
    h = ExperimentRecord.compute_dataset_hash("test_data")
    assert isinstance(h, str)
    assert len(h) == 64  # SHA-256