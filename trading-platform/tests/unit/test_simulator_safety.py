from datetime import datetime, timezone

import pytest
from trading_platform.domain import Bar, Instrument, OrderSide, OrderType, Signal, TimeInForce
from trading_platform.simulator.event_driven_simulator import EventDrivenSimulator, FillAssumption


def bar(day: int, opening: float, high: float, low: float, close: float) -> Bar:
    return Bar(Instrument("AAPL"), datetime(2026, 1, day, tzinfo=timezone.utc), opening, high, low, close, 1000)


def signal(order_type: OrderType, price: float) -> Signal:
    return Signal(Instrument("AAPL"), OrderSide.BUY, 1, price, order_type, TimeInForce.DAY)


def test_completed_close_signal_fills_only_at_next_open() -> None:
    with pytest.raises(ValueError):
        EventDrivenSimulator(fill_assumption=FillAssumption.CLOSE)
    result = EventDrivenSimulator().run(
        [bar(1, 100, 101, 99, 100), bar(2, 110, 112, 109, 111)],
        [Signal(Instrument("AAPL"), OrderSide.BUY, 1, None, OrderType.MARKET, TimeInForce.DAY)],
    )
    assert result.trade_ledger[0].detail["fill_price"] == 110
    assert result.trade_ledger[0].timestamp.day == 2


def test_limit_and_stop_orders_require_bar_eligibility() -> None:
    limit_signal = signal(OrderType.LIMIT, 105)
    no_fill = EventDrivenSimulator().run([bar(1, 100, 101, 99, 100), bar(2, 110, 112, 109, 111)], [limit_signal])
    assert not no_fill.trade_ledger
    eligible = EventDrivenSimulator().run([bar(1, 100, 101, 99, 100), bar(2, 110, 112, 104, 111)], [limit_signal])
    assert eligible.trade_ledger[0].detail["fill_price"] == 105
    stop_signal = signal(OrderType.STOP, 105)
    gap = EventDrivenSimulator().run([bar(1, 100, 101, 99, 100), bar(2, 110, 112, 109, 111)], [stop_signal])
    assert gap.trade_ledger[0].detail["fill_price"] == 110
