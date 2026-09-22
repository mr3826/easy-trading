"""Phase 6 walk-forward module tests."""

from datetime import datetime

import pytest
from trading_platform.persistence.experiment import ExperimentRecord
from trading_platform.strategies.ma_cross_strategy import MaCrossHypothesis
from trading_platform.walk_forward.walk_forward import PeriodSplit


def test_period_split_creation():
    """Test PeriodSplit splits with exact chronological arithmetic."""
    split = PeriodSplit(train_days=60, validation_days=20, test_days=30)
    result = split.split(datetime(2020, 1, 1).date(), datetime(2020, 12, 31).date())
    train, val, test = result["train"], result["validation"], result["test"]
    assert train == (datetime(2020, 1, 1).date(), datetime(2020, 2, 29).date())
    assert val == (datetime(2020, 3, 1).date(), datetime(2020, 3, 19).date())
    assert test == (datetime(2020, 3, 20).date(), datetime(2020, 4, 17).date())
    assert train[1] < val[0] <= val[1] < test[0] <= test[1]


def test_walk_forward_folds():
    """Test walk-forward iterator: fold count, ordering, forward-shifting test windows."""
    split = PeriodSplit(train_days=60, validation_days=20, test_days=30)
    folds = split.walk_forward(datetime(2020, 1, 1).date(), datetime(2020, 12, 31).date(), step_forward=30)
    assert len(folds) > 0
    for earlier, later in zip(folds, folds[1:]):
        assert earlier["train"][0] < later["train"][0]
        assert earlier["test"][0] < later["test"][0]
    for fold in folds:
        assert fold["train"][1] < fold["validation"][0] <= fold["validation"][1] < fold["test"][0]


def test_get_test_period():
    """Test that the fixed test period is consistent and exact."""
    split = PeriodSplit(train_days=60, validation_days=20, test_days=30)
    test_period = split.get_test_period([datetime(2020, 1, 1).date(), datetime(2025, 12, 31).date()])
    assert test_period == (datetime(2020, 3, 20).date(), datetime(2020, 4, 17).date())


def test_split_rejects_data_too_short():
    """A range too short for the requested split sizes raises."""
    split = PeriodSplit(train_days=30, validation_days=10, test_days=15)
    with pytest.raises(ValueError):
        split.split(datetime(2020, 1, 1).date(), datetime(2020, 1, 20).date())


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
