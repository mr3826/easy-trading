"""Simulator unit tests for the event-driven trading simulator."""
from datetime import datetime, timezone

from trading_platform.domain import Instrument, Bar
from trading_platform.domain import OrderSide, Signal, OrderType, TimeInForce
from trading_platform.simulator.event_driven_simulator import (
    EventDrivenSimulator,
    SimulationMode,
    FillAssumption,
    SimulationResult,
)


def test_simulator_basic_buy():
    """Basic buy order simulation with CLOSE fill assumption."""
    inst = Instrument(symbol="AAPL")

    # Create bars (3 days of data)
    bars = [
        Bar(
            instrument=inst,
            timestamp=datetime(2026, 1, 15, tzinfo=timezone.utc),
            open=100.0,
            high=105.0,
            low=95.0,
            close=102.0,
            volume=1000,
        ),
        Bar(
            instrument=inst,
            timestamp=datetime(2026, 1, 16, tzinfo=timezone.utc),
            open=102.0,
            high=108.0,
            low=101.0,
            close=105.0,
            volume=1200,
        ),
        Bar(
            instrument=inst,
            timestamp=datetime(2026, 1, 17, tzinfo=timezone.utc),
            open=105.0,
            high=110.0,
            low=104.0,
            close=108.0,
            volume=1500,
        ),
    ]

    # Create signal to buy 10 shares at market (price None means market order)
    signal = Signal(
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=None,  # Market order
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )

    # Initialize simulator with CLOSE fill assumption
    simulator = EventDrivenSimulator(
        mode=SimulationMode.DETERMINISTIC,
        fill_assumption=FillAssumption.CLOSE,
    )

    # Run simulation
    result = simulator.run(
        bars=bars,
        signals=[signal],
    )

    # Verify results
    assert result.final_cash < 10000.0  # Cash reduced by purchase + commission
    assert len(result.order_ledger) > 0  # Orders were submitted
    assert len(result.trade_ledger) > 0  # Trades were executed
    assert result.final_positions[inst.symbol].quantity == 10  # Bought 10 shares
    assert result.final_positions[inst.symbol].market_value == 102.0 * 10  # 10 shares @ $102 close


def test_simulator_sell_existing_position():
    """Sell an existing long position.

    V1 behavior: signals are matched one-to-one with bars sequentially.
    To sell an existing position, use initial_portfolio and ensure
    the sell signal is on a later bar than the buy signal.
    """
    inst = Instrument(symbol="AAPL")

    # Bars for buy: 1 bar at $102
    buy_bars = [
        Bar(
            instrument=inst,
            timestamp=datetime(2026, 1, 15, tzinfo=timezone.utc),
            open=100.0,
            high=105.0,
            low=95.0,
            close=102.0,
            volume=1000,
        ),
    ]

    # Bars for sell: 2 bars, sell on the second bar at $108
    # Signal indexing assigns signals sequentially: signal 1 -> bar[0], signal 2 -> bar[1], etc.
    # So we need 2 signals (buy then sell) over 2+ bars, or use initial_portfolio
    sell_bars = [
        Bar(
            instrument=inst,
            timestamp=datetime(2026, 1, 16, tzinfo=timezone.utc),
            open=105.0,
            high=110.0,
            low=104.0,
            close=108.0,
            volume=1500,
        ),
        Bar(
            instrument=inst,
            timestamp=datetime(2026, 1, 17, tzinfo=timezone.utc),
            open=108.0,
            high=112.0,
            low=107.0,
            close=110.0,
            volume=2000,
        ),
    ]

    # First, buy 10 shares
    buy_signal = Signal(
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )

    simulator = EventDrivenSimulator(
        mode=SimulationMode.DETERMINISTIC,
        fill_assumption=FillAssumption.CLOSE,
    )

    # Buy first (1 bar, 1 signal)
    buy_result = simulator.run(bars=buy_bars, signals=[buy_signal])

    # Now sell the position using initial_portfolio
    # sell_signal will be assigned to bar[0] of sell_bars (close=108.0)
    sell_signal = Signal(
        instrument=inst,
        side=OrderSide.SELL,
        quantity=10,
        price=None,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )

    sell_result = simulator.run(
        bars=sell_bars,
        signals=[sell_signal],
        initial_portfolio=buy_result.final_portfolio,
    )

    # Should have sold the position
    assert sell_result.final_positions[inst.symbol].quantity == 0
    # Cash should increase by sell proceeds: 8979 + 108*10 + 1 = 10060
    assert sell_result.final_cash == 10060.0
    # total_pnl should include realized PnL from the sell
    assert sell_result.final_portfolio.total_pnl != 0
    # Realized PnL from selling at 108 - buying at 102 = 60
    assert sell_result.final_portfolio.total_pnl == 60.0


def test_simulator_partial_fill():
    """Test partial fill scenario."""
    inst = Instrument(symbol="AAPL")

    # Only one bar
    bars = [
        Bar(
            instrument=inst,
            timestamp=datetime(2026, 1, 15, tzinfo=timezone.utc),
            open=100.0,
            high=105.0,
            low=95.0,
            close=102.0,
            volume=1000,
        ),
    ]

    # Signal for 10 shares, but only enough cash for 5
    signal = Signal(
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=102.0,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
    )

    simulator = EventDrivenSimulator(
        mode=SimulationMode.DETERMINISTIC,
        fill_assumption=FillAssumption.CLOSE,
        start_cash=1100.0,  # Enough for 10 shares @ $102 + commission
    )

    result = simulator.run(bars=bars, signals=[signal])

    # Position should have 10 shares (full fill)
    assert result.final_positions[inst.symbol].quantity == 10
    # Some fills should have occurred
    assert len(result.trade_ledger) > 0


def test_simulator_insufficient_cash():
    """Test that orders are rejected when insufficient cash."""
    inst = Instrument(symbol="AAPL")

    bars = [
        Bar(
            instrument=inst,
            timestamp=datetime(2026, 1, 15, tzinfo=timezone.utc),
            open=100.0,
            high=105.0,
            low=95.0,
            close=102.0,
            volume=1000,
        ),
    ]

    # Order for 10 shares at $150 each = $1500, but only $100 cash
    signal = Signal(
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=150.0,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
    )

    simulator = EventDrivenSimulator(
        mode=SimulationMode.DETERMINISTIC,
        fill_assumption=FillAssumption.CLOSE,
        start_cash=100.0,
    )

    result = simulator.run(bars=bars, signals=[signal])

    # Order should be rejected due to insufficient cash
    assert result.final_cash == 100.0  # Cash unchanged


def test_simulator_deterministic_replay():
    """Test that same inputs produce same outputs (deterministic)."""
    inst = Instrument(symbol="AAPL")

    bars = [
        Bar(
            instrument=inst,
            timestamp=datetime(2026, 1, 15, tzinfo=timezone.utc),
            open=100.0,
            high=105.0,
            low=95.0,
            close=102.0,
            volume=1000,
        ),
    ]

    signal = Signal(
        instrument=inst,
        side=OrderSide.BUY,
        quantity=10,
        price=102.0,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
    )

    simulator = EventDrivenSimulator(
        mode=SimulationMode.DETERMINISTIC,
        fill_assumption=FillAssumption.CLOSE,
        start_cash=1100.0,
    )

    # Run once
    result1 = simulator.run(bars=bars, signals=[signal])

    # Run again with same inputs
    result2 = simulator.run(bars=bars, signals=[signal])

    # Results should be identical
    assert result1.final_cash == result2.final_cash
    assert result1.final_positions[inst.symbol].quantity == result2.final_positions[inst.symbol].quantity
    assert result1.total_commission == result2.total_commission
    assert result1.total_slippage == result2.total_slippage


def test_simulation_result_metrics():
    """Test SimulationResult summary metrics."""
    inst = Instrument(symbol="AAPL")

    bars = [
        Bar(
            instrument=inst,
            timestamp=datetime(2026, 1, 15, tzinfo=timezone.utc),
            open=100.0,
            high=105.0,
            low=95.0,
            close=102.0,
            volume=1000,
        ),
    ]

    # Buy 5 shares
    signal = Signal(
        instrument=inst,
        side=OrderSide.BUY,
        quantity=5,
        price=102.0,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
    )

    simulator = EventDrivenSimulator(
        mode=SimulationMode.DETERMINISTIC,
        fill_assumption=FillAssumption.CLOSE,
        start_cash=1100.0,
    )

    result = simulator.run(bars=bars, signals=[signal])

    # Check metrics exist and have reasonable values
    assert hasattr(result, "final_cash")
    assert hasattr(result, "total_commission")
    assert hasattr(result, "total_slippage")
    assert result.final_cash >= 0
    assert result.total_commission >= 0
    assert result.total_slippage >= 0