"""Debug signal indexing."""
from trading_platform.domain import Instrument, Bar, OrderSide, Signal, OrderType, TimeInForce
from trading_platform.simulator.event_driven_simulator import EventDrivenSimulator, SimulationMode, FillAssumption
from datetime import datetime, timezone

inst = Instrument(symbol="AAPL")

# Create bars - 3 bars
bars = [
    Bar(instrument=inst, timestamp=datetime(2026, 1, 15, tzinfo=timezone.utc), open=100.0, high=105.0, low=95.0, close=102.0, volume=1000),
    Bar(instrument=inst, timestamp=datetime(2026, 1, 16, tzinfo=timezone.utc), open=102.0, high=108.0, low=101.0, close=105.0, volume=1200),
    Bar(instrument=inst, timestamp=datetime(2026, 1, 17, tzinfo=timezone.utc), open=105.0, high=110.0, low=104.0, close=108.0, volume=1500),
]

# Buy signal (first)
buy_signal = Signal(instrument=inst, side=OrderSide.BUY, quantity=10, price=None,
                    order_type=OrderType.MARKET, time_in_force=TimeInForce.DAY)

# Sell signal (second)
sell_signal = Signal(instrument=inst, side=OrderSide.SELL, quantity=10, price=None,
                     order_type=OrderType.MARKET, time_in_force=TimeInForce.DAY)

sim = EventDrivenSimulator(mode=SimulationMode.DETERMINISTIC, fill_assumption=FillAssumption.CLOSE, start_cash=10000.0)

print("=== RUN WITH 3 BARS, 2 SIGNALS (buy then sell) ===")
result = sim.run(bars=bars, signals=[buy_signal, sell_signal])
print(f"final_cash: {result.final_cash}")
print(f"final_positions: {result.final_positions}")
print(f"trade_ledger: {result.trade_ledger}")
print(f"order_ledger types: {[e.event_type for e in result.order_ledger]}")

# Now test with initial_portfolio
sim2 = EventDrivenSimulator(mode=SimulationMode.DETERMINISTIC, fill_assumption=FillAssumption.CLOSE, start_cash=10000.0)

print("\n=== RUN WITH 3 BARS, 2 SIGNALS + initial_portfolio ===")
# First buy
buy_result = sim2.run(bars=bars[:1], signals=[buy_signal])
print(f"Buy cash: {buy_result.final_cash}, positions: {buy_result.final_positions}")

# Now sell with initial_portfolio
extended_bars = bars  # 3 bars
sell_result = sim2.run(bars=extended_bars, signals=[sell_signal], initial_portfolio=buy_result.final_portfolio)
print(f"Sell cash: {sell_result.final_cash}")
print(f"Sell positions: {sell_result.final_positions}")
print(f"trade_ledger: {sell_result.trade_ledger}")
print(f"order_ledger types: {[e.event_type for e in sell_result.order_ledger]}")