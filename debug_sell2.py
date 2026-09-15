"""More detailed debug for sell test."""
from trading_platform.domain import Instrument, Bar, OrderSide, Signal, OrderType, TimeInForce
from trading_platform.simulator.event_driven_simulator import EventDrivenSimulator, SimulationMode, FillAssumption, OrderStatus
from datetime import datetime, timezone
from decimal import Decimal

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

sim = EventDrivenSimulator(mode=SimulationMode.DETERMINISTIC, fill_assumption=FillAssumption.CLOSE, start_cash=10000.0)
buy_result = sim.run(bars=bars[:1], signals=[buy_signal])

print("=== BUY RESULT ===")
print(f"final_cash: {buy_result.final_cash}")
print(f"final_positions: {buy_result.final_positions}")
print(f"final_portfolio.cash: {buy_result.final_portfolio.cash}")
print(f"final_portfolio.positions: {buy_result.final_portfolio.positions}")
print(f"trade_ledger: {buy_result.trade_ledger}")
print(f"order_ledger: {[e.event_type for e in buy_result.order_ledger]}")

# Now sell - pass initial portfolio
sell_signal = Signal(instrument=inst, side=OrderSide.SELL, quantity=10, price=None,
                     order_type=OrderType.MARKET, time_in_force=TimeInForce.DAY)

extended_bars = bars + [
    Bar(instrument=inst, timestamp=datetime(2026, 1, 16, tzinfo=timezone.utc), open=105.0, high=110.0, low=104.0, close=108.0, volume=1500)
]

print("\n=== SELL WITH initial_portfolio ===")
sell_result = sim.run(bars=extended_bars, signals=[sell_signal], initial_portfolio=buy_result.final_portfolio)

print(f"final_cash: {sell_result.final_cash}")
print(f"final_positions: {sell_result.final_positions}")
print(f"final_portfolio.cash: {sell_result.final_portfolio.cash}")
print(f"final_portfolio.positions: {sell_result.final_portfolio.positions}")
print(f"trade_ledger: {sell_result.trade_ledger}")
print(f"order_ledger: {[e.event_type for e in sell_result.order_ledger]}")
print(f"fill_count: {sim.fill_count}")
print(f"total_commission: {sim.total_commission}")

# Now try without initial_portfolio - fresh start
print("\n=== SELL WITHOUT initial_portfolio ===")
sim2 = EventDrivenSimulator(mode=SimulationMode.DETERMINISTIC, fill_assumption=FillAssumption.CLOSE, start_cash=10000.0)
# First buy
buy_result2 = sim2.run(bars=bars[:1], signals=[buy_signal])
print(f"Buy cash: {buy_result2.final_cash}, positions: {buy_result2.final_positions}")

# Now sell without passing initial_portfolio - but this won't have the position...
# Actually the run method would reset positions
sell_result2 = sim2.run(bars=extended_bars, signals=[sell_signal])
print(f"Sell cash: {sell_result2.final_cash}, positions: {sell_result2.final_positions}")
print(f"trade_ledger: {sell_result2.trade_ledger}")
print(f"order_ledger: {[e.event_type for e in sell_result2.order_ledger]}")