"""Debug script for sell test."""
from trading_platform.domain import Instrument, Bar, OrderSide, Signal, OrderType, TimeInForce
from trading_platform.simulator.event_driven_simulator import EventDrivenSimulator, SimulationMode, FillAssumption
from datetime import datetime, timezone

inst = Instrument(symbol="AAPL")

# Create bars
bars = [
    Bar(
        instrument=inst,
        timestamp=datetime(2026, 1, 15, tzinfo=timezone.utc),
        open=100.0, high=105.0, low=95.0, close=102.0, volume=1000,
    ),
    Bar(
        instrument=inst,
        timestamp=datetime(2026, 1, 16, tzinfo=timezone.utc),
        open=102.0, high=108.0, low=101.0, close=105.0, volume=1200,
    ),
]

# Buy 10 shares
buy_signal = Signal(instrument=inst, side=OrderSide.BUY, quantity=10, price=None,
                    order_type=OrderType.MARKET, time_in_force=TimeInForce.DAY)

sim = EventDrivenSimulator(mode=SimulationMode.DETERMINISTIC, fill_assumption=FillAssumption.CLOSE)
buy_result = sim.run(bars=bars[:1], signals=[buy_signal])

print("Buy result final_cash:", buy_result.final_cash)
print("Buy result final_positions:", buy_result.final_positions)
print("Buy result final_portfolio:", buy_result.final_portfolio)
print("Buy result trade_ledger:", buy_result.trade_ledger)
print("Buy result order_ledger:", buy_result.order_ledger)

# Now sell
sell_signal = Signal(instrument=inst, side=OrderSide.SELL, quantity=10, price=None,
                     order_type=OrderType.MARKET, time_in_force=TimeInForce.DAY)

extended_bars = bars + [
    Bar(instrument=inst, timestamp=datetime(2026, 1, 16, tzinfo=timezone.utc), open=105.0, high=110.0, low=104.0, close=108.0, volume=1500)
]

sell_result = sim.run(bars=extended_bars, signals=[sell_signal], initial_portfolio=buy_result.final_portfolio)

print("\nSell result final_cash:", sell_result.final_cash)
print("Sell result final_positions:", sell_result.final_positions)
print("Sell result final_portfolio:", sell_result.final_portfolio)
print("Sell result trade_ledger:", sell_result.trade_ledger)
print("Sell result order_ledger:", sell_result.order_ledger)