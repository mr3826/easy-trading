"""Phase 4 tests for the AI Trading Platform."""

from datetime import datetime
from pathlib import Path

from trading_platform.strategies.ma_cross_strategy import (
    MaCrossHypothesis,
    generate_signal,
    hypothesis_to_dict,
    dict_to_hypothesis,
)
from trading_platform.risk.limits import check_position_limit, check_buying_power
from trading_platform.domain import Instrument, Position
from trading_platform.persistence.experiment import (
    ExperimentRecord,
    ExperimentRegistry,
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
        fast_length=5,
        slow_length=20,
        max_positions=3,
        commission_per_order=1.0,
        slippage_pct=0.001,
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

    # A fourth new position must be rejected (max is 3)
    full_book = {
        sym: Position(Instrument(sym), 10, 100.0, 1000.0, 0.0, 0.0)
        for sym in ("AAPL", "MSFT", "GOOGL")
    }
    ok4, reason4 = check_position_limit(1, full_book, Instrument(symbol="TSLA"))
    assert ok4 is False
    assert "Max positions" in reason4


def test_buying_power():
    """Test buying power check."""
    # Enough cash
    ok, reason = check_buying_power(10, 100.0, 1500.0, {}, 1.0)
    assert ok is True
    assert "Insufficient" not in reason

    # Not enough cash
    ok2, reason2 = check_buying_power(100, 100.0, 500.0, {}, 1.0)
    assert ok2 is False
    assert "Insufficient" in reason2


def test_experiment_record(tmp_path):
    """Test ExperimentRecord creation and registry with real provenance hashes."""
    h = MaCrossHypothesis(fast_length=5, slow_length=20)
    seed = 42
    reg = ExperimentRegistry(root=tmp_path / "experiments")

    exp_id = ExperimentRecord.new_experiment_id()
    lock_hash = ExperimentRecord.compute_dependency_lock_hash(Path(__file__).resolve().parents[3] / "uv.lock")
    policy_hash = ExperimentRecord.compute_policy_hash({"max_positions": 3, "version": "0.1-PROVISIONAL"})
    rec = ExperimentRecord(
        experiment_id=exp_id,
        hypothesis=h,
        dataset_hash=ExperimentRecord.compute_dataset_hash("test_data"),
        universe_version="v1.0",
        code_commit=ExperimentRecord.compute_code_hash(),
        dependency_lock=lock_hash,
        policy_hash=policy_hash,
        cost_slippage_assumptions={"commission": 1.0, "slippage_pct": 0.001},
        seed=seed,
        result_metrics={"return": 0.05, "sharpe": 1.2},
    )
    reg.register(rec)
    loaded = reg.get(exp_id)
    assert loaded is not None
    assert loaded.hypothesis.hypothesis_id == h.hypothesis_id
    assert loaded.result_metrics["return"] == 0.05
    assert loaded.dataset_hash == ExperimentRecord.compute_dataset_hash("test_data")
    assert len(loaded.code_commit) in (40, 64)
    assert loaded.dependency_lock == lock_hash
    assert loaded.policy_hash == policy_hash


def test_metrics():
    """Test performance metrics computation."""
    pnls = [10.0, -5.0, 20.0, -15.0, 30.0]

    ew = [p for p in pnls if p > 0]
    lw = [p for p in pnls if p < 0]

    exp = compute_expectancy(ew, lw)
    assert exp is not None
    # expectancy = (3/5 * 20) - (2/5 * -10) = 12 + 4 = 16
    # (win_rate * avg_win) - (loss_rate * avg_loss) with losses [-5, -15]
    assert abs(exp - 16.0) < 0.001

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


def test_concentration():
    """Test concentration computation."""
    from trading_platform.domain import Position

    positions = {
        "AAPL": Position(
            instrument=Instrument(symbol="AAPL"),
            quantity=10,
            average_cost=100.0,
            market_value=1000.0,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
            last_update=datetime(2024, 1, 1),
        ),
        "MSFT": Position(
            instrument=Instrument(symbol="MSFT"),
            quantity=5,
            average_cost=200.0,
            market_value=1000.0,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
            last_update=datetime(2024, 1, 1),
        ),
    }

    # simplify: just check the function can run
    # with Position dataclass objects
    conc = compute_concentration(positions)
    assert "herfindahl_index" in conc
    assert "top_symbol" in conc
    assert "top_pct" in conc
    assert conc["top_symbol"] in ("AAPL", "MSFT")
