"""Test script for Phase 4 modules."""
from trading_platform.strategies.ma_cross_strategy import (
    MaCrossHypothesis,
    generate_signal,
    hypothesis_to_dict,
    dict_to_hypothesis,
)
from trading_platform.risk.limit import check_position_limit, check_buying_power
from trading_platform.domain import Instrument
from trading_platform.persistence.experiment import (
    ExperimentRecord,
    ExperimentRegistry,
    compute_dataset_hash,
    compute_code_hash,
)
from trading_platform.persistence.baseline_report import (
    EngineeringBaselineReport,
    compute_expectancy,
    compute_profit_factor,
    compute_sharpe,
    compute_max_drawdown,
    compute_win_loss_distribution,
    compute_concentration,
)


def test_hypothesis():
    """Test MaCrossHypothesis creation and serialization."""
    h = MaCrossHypothesis(
        fast_length=5, slow_length=20, max_positions=3,
        commission_per_order=1.0, slippage_pct=0.001
    )
    assert h.fast_length == 5
    assert h.slow_length == 20
    assert h.max_positions == 3
    assert h.commission_per_order == 1.0
    assert h.slippage_pct == 0.001

    # Test serialization round-trip
    d = hypothesis_to_dict(h)
    h2 = dict_to_hypothesis(d)
    assert h.hypothesis_id == h2.hypothesis_id
    assert h.fast_length == h2.fast_length
    assert h.slow_length == h2.slow_length

    print("PASS: test_hypothesis")


def test_position_limit():
    """Test position limit checking."""
    inst = Instrument(symbol="AAPL")
    pos_dict = {}

    # New position should be allowed
    ok, reason = check_position_limit(1, pos_dict, inst)
    assert ok is True
    assert "Max positions" not in reason

    # Two new positions should still be OK (max is 3)
    ok2, reason2 = check_position_limit(1, pos_dict, Instrument(symbol="MSFT"))
    assert ok2 is True

    # Three new positions should be OK
    ok3, reason3 = check_position_limit(1, pos_dict, Instrument(symbol="GOOGL"))
    assert ok3 is True

    print("PASS: test_position_limit")


def test_buying_power():
    """Test buying power check."""
    # Enough cash
    ok, reason = check_buying_power(10, 100.0, 1500.0, {})
    assert ok is True
    assert "Insufficient" not in reason

    # Not enough cash
    ok2, reason2 = check_buying_power(100, 100.0, 500.0, {})
    assert ok2 is False
    assert "Insufficient" in reason2

    print("PASS: test_buying_power")


def test_experiment_record():
    """Test ExperimentRecord creation and registry."""
    from trading_platform.strategies.ma_cross_strategy import MaCrossHypothesis

    h = MaCrossHypothesis(fast_length=5, slow_length=20)
    seed = 42
    reg = ExperimentRegistry()

    exp_id = ExperimentRecord.new_experiment_id()
    rec = ExperimentRecord(
        experiment_id=exp_id,
        hypothesis=h,
        dataset_hash=compute_dataset_hash("test_data"),
        universe_version="v1.0",
        code_commit=compute_code_hash(),
        dependency_lock="dep-lock-123",
        cost_slippage_assumptions={"commission": 1.0, "slippage_pct": 0.001},
        seed=seed,
        result_metrics={"return": 0.05, "sharpe": 1.2},
    )
    reg.register(rec)
    loaded = reg.get(exp_id)
    assert loaded is not None
    assert loaded.hypothesis.hypothesis_id == h.hypothesis_id
    assert loaded.result_metrics["return"] == 0.05

    print("PASS: test_experiment_record")


def test_metrics():
    """Test performance metrics computation."""
    pnls = [10.0, -5.0, 20.0, -15.0, 30.0]

    ew = [p for p in pnls if p > 0]
    lw = [p for p in pnls if p < 0]

    exp = compute_expectancy(ew, lw)
    assert exp is not None
    assert abs(exp - 8.0) < 0.001  # (3/5 * 20) - (2/5 * 10) = 12 - 4 = 8

    pf = compute_profit_factor(ew, lw)
    assert pf is not None
    assert abs(pf - 3.0) < 0.001  # gross wins 50 / gross losses 16.67 approx 3.0

    sharpe = compute_sharpe([0.01, -0.02, 0.03, -0.01, 0.04])
    assert sharpe is not None

    dd = compute_max_drawdown([100.0, 105.0, 102.0, 95.0, 98.0, 103.0])
    assert dd > 0

    wl = compute_win_loss_distribution(pnls)
    assert wl["wins"] == 3
    assert wl["losses"] == 2
    assert wl["total"] == 5

    print("PASS: test_metrics")


def test_concentration():
    """Test concentration computation."""
    import datetime

    positions = {
        "AAPL": {
            "instrument": Instrument(symbol="AAPL"),
            "quantity": 10,
            "average_cost": 100.0,
            "market_value": 1000.0,
            "unrealized_pnl": 0.0,
            "realized_pnl": 0.0,
            "last_update": datetime.datetime.now(),
        },
        "MSFT": {
            "instrument": Instrument(symbol="MSFT"),
            "quantity": 5,
            "average_cost": 200.0,
            "market_value": 1000.0,
            "unrealized_pnl": 0.0,
            "realized_pnl": 0.0,
            "last_update": datetime.datetime.now(),
        },
    }

    # simplify: just check the function can run
    # with raw dicts since Position is a dataclass
    conc = compute_concentration(positions)
    assert "herfindahl_index" in conc
    assert "top_symbol" in conc
    assert "top_pct" in conc
    assert conc["top_symbol"] in ("AAPL", "MSFT")

    print("PASS: test_concentration")


def main():
    test_hypothesis()
    test_position_limit()
    test_buying_power()
    test_experiment_record()
    test_metrics()
    test_concentration()
    print("\nAll Phase 4 tests passed!")


if __name__ == "__main__":
    main()