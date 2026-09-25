"""Debug _execute_order directly."""
from trading_platform.domain import Instrument, Bar, OrderSide, Signal, OrderType, TimeInForce, Order, OrderStatus, Position
from trading_platform.simulator.event_driven_simulator import EventDrivenSimulator, SimulationMode, FillAssumption
from datetime import datetime, timezone
from decimal import Decimal

inst = Instrument(symbol="AAPL")

# Create bars
bars = [
    Bar(instrument=inst, timestamp=datetime(2026, 1, 15, tzinfo=timezone.utc), open=100.0, high=105.0, low=95.0, close=102.0, volume=1000),
    Bar(instrument=inst, timestamp=datetime(2026, 1, 16, tzinfo=timezone.utc), open=102.0, high=108.0, low=101.0, close=105.0, volume=1200),
    Bar(instrument=inst, timestamp=datetime(2026, 1, 17, tzinfo=timezone.utc), open=105.0, high=110.0, low=104.0, close=108.0, volume=1500),
]

# Buy 10 shares
buy_signal = Signal(instrument=inst, side=OrderSide.BUY, quantity=10, price=None,
                    order_type=OrderType.MARKET, time_in_force=TimeInForce.DAY)

sim = EventDrivenSimulator(mode=SimulationMode.DETERMINISTIC, fill_assumption=FillAssumption.CLOSE, start_cash=10000.0)
buy_result = sim.run(bars=bars[:1], signals=[buy_signal])

# Manually set up sim state like it would be with initial_portfolio
sim.cash = 8979.0
sim.positions = {'AAPL': Position(instrument=inst, quantity=10, average_cost=102.0, market_value=1020.0, unrealized_pnl=0.0, realized_pnl=0.0, last_update=datetime.min.replace(tzinfo=timezone.utc))}

# Create a proper Order object
order = Order(
    order_id="test-order-1",
    instrument=inst,
    side=OrderSide.SELL,
    quantity=10,
    price=None,
    order_type=OrderType.MARKET,
    time_in_force=TimeInForce.DAY,
    status=OrderStatus.SUBMITTED,
    signal=buy_signal,
    risk_decision=None,
)

# Manually call _calculate_fill_price
bar = bars[2]  # third bar, close=108.0
fill_price = sim._calculate_fill_price(order.price, bar, order.side)
print(f"fill_price from _calculate_fill_price: {fill_price}")

# Call _quantize_shares
actual_qty = sim._quantize_shares(order.quantity, order.side, fill_price, bar)
print(f"actual_qty from _quantize_shares: {actual_qty}")

# Now call _execute_order
fill_result = sim._execute_order(order, bar)
print(f"fill_result.fill_quantity: {fill_result.fill_quantity}")
print(f"fill_result.fill_price: {fill_result.fill_price}")
print(f"fill_result.fill_commission: {fill_result.fill_commission}")
print(f"sim.cash after: {sim.cash}")
print(f"sim.positions: {sim.positions}")
print(f"order_ledger: {[e.event_type for e in sim.order_ledger]}")
print(f"trade_ledger: {[e.event_type for e in sim.trade_ledger]}")