"""Debug full sell run."""
from trading_platform.domain import Instrument, Bar, OrderSide, Signal, OrderType, TimeInForce, Order, OrderStatus, Position
from trading_platform.simulator.event_driven_simulator import (
    EventDrivenSimulator, SimulationMode, FillAssumption, SimulationResult
)
from datetime import datetime, timezone

inst = Instrument(symbol="AAPL")

# Create bars
bars = [
    Bar(instrument=inst, timestamp=datetime(2026, 1, 15, tzinfo=timezone.utc), open=100.0, high=105.0, low=95.0, close=102.0, volume=1000),
    Bar(instrument=inst, timestamp=datetime(2026, 1, 16, tzinfo=timezone.utc), open=102.0, high=108.0, low=101.0, close=105.0, volume=1200),
]

# Buy 10 shares
buy_signal = Signal(instrument=inst, side=OrderSide.BUY, quantity=10, price=None,
                    order_type=OrderType.MARKET, time_in_force=TimeInForce.DAY)

sim = EventDrivenSimulator(mode=SimulationMode.DETERMINISTIC, fill_assumption=FillAssumption.CLOSE, start_cash=10000.0)
buy_result = sim.run(bars=bars[:1], signals=[buy_signal])

print("=== BUY RESULT ===")
print(f"final_cash: {buy_result.final_cash}")
print(f"final_positions: {buy_result.final_positions}")
print(f"final_portfolio: {buy_result.final_portfolio}")
print(f"trade_ledger: {buy_result.trade_ledger}")
print(f"order_ledger: {[e.event_type for e in buy_result.order_ledger]}")

# Now sell - pass initial portfolio
sell_signal = Signal(instrument=inst, side=OrderSide.SELL, quantity=10, price=None,
                     order_type=OrderType.MARKET, time_in_force=TimeInForce.DAY)

extended_bars = bars + [
    Bar(instrument=inst, timestamp=datetime(2026, 1, 16, tzinfo=timezone.utc), open=105.0, high=110.0, low=104.0, close=108.0, volume=1500)
]

# Monkey-patch to add debugging
original_execute = sim._execute_order
def debug_execute(order, bar):
    print(f"\n=== _execute_order called ===")
    print(f"order.quantity: {order.quantity}")
    print(f"order.side: {order.side}")
    print(f"order.price: {order.price}")
    print(f"bar.close: {bar.close}")
    print(f"sim.cash before: {sim.cash}")
    print(f"sim.positions: {sim.positions}")
    
    result = original_execute(order, bar)
    
    print(f"fill_result.fill_quantity: {result.fill_quantity}")
    print(f"fill_result.fill_price: {result.fill_price}")
    print(f"fill_result.fill_commission: {result.fill_commission}")
    print(f"sim.cash after: {sim.cash}")
    print(f"sim.positions: {sim.positions}")
    print(f"order_ledger: {[e.event_type for e in sim.order_ledger]}")
    print(f"trade_ledger: {[e.event_type for e in sim.trade_ledger]}")
    return result

sim._execute_order = debug_execute

print("\n=== SELL WITH initial_portfolio ===")
sell_result = sim.run(bars=extended_bars, signals=[sell_signal], initial_portfolio=buy_result.final_portfolio)

print(f"\n=== FINAL RESULTS ===")
print(f"final_cash: {sell_result.final_cash}")
print(f"final_positions: {sell_result.final_positions}")
print(f"final_portfolio: {sell_result.final_portfolio}")
print(f"trade_ledger: {sell_result.trade_ledger}")
print(f"order_ledger: {[e.event_type for e in sell_result.order_ledger]}")
print(f"fill_count: {sim.fill_count}")