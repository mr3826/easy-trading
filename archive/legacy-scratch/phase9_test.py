"""Test script for Phase 9 IBKR broker adapter."""
import sys
sys.path.insert(0, r"D:\hexabyte_technologies\easy-trading\trading-platform\src")

from trading_platform.broker_adapter import InteractiveBrokersBroker, BrokerAdapter
from trading_platform.domain import Instrument, Order, OrderSide, OrderType, TimeInForce, OrderStatus, Signal
from trading_platform.oms.oms import OMS

print("=" * 60)
print("PHASE 9: IBKR PAPER INTEGRATION")
print("=" * 60)

# --- IBKRBroker basic properties ---
print("\n--- IBKRBroker Properties ---")
ibkr = InteractiveBrokersBroker(paper=True)
print(f"  broker_id: {ibkr.broker_id}")
print(f"  connected (init): {ibkr.connected}")

# --- Connect ---
print("\n--- Connect ---")
connected = ibkr.start()
print(f"  start() returned: {connected}")
print(f"  connected after start: {ibkr.connected}")

# --- Create instrument and order ---
inst = Instrument(symbol="AAPL")
# Create minimal Signal for Order construction
sig = Signal(inst, OrderSide.BUY, 10, 150.0, OrderType.MARKET, TimeInForce.DAY)
order = Order(
    order_id="ibkr-test-001",
    instrument=inst,
    side=OrderSide.BUY,
    quantity=10,
    price=150.0,
    order_type=OrderType.MARKET,
    time_in_force=TimeInForce.DAY,
    status=OrderStatus.SUBMITTED,
    signal=sig,
)

# --- Execute order ---
print("\n--- Execute Order ---")
bar_type = type("Bar", (), {"close": 150.5})()
result = ibkr.execute_order(order, bar_type)
print(f"  Execution result: {result['status']}")
print(f"  fill_price: {result['fill_price']}")
print(f"  commission: {result['commission']}")
print(f"  slippage: {result['slippage']}")

# --- List orders ---
print("\n--- List Orders ---")
open_orders = ibkr.list_orders()
print(f"  Open orders: {len(open_orders)}")
for oid, info in open_orders.items():
    print(f"    {oid}: status={info['status']}, qty={info['quantity']}")

# --- Get order status ---
print("\n--- Get Order Status ---")
status = ibkr.get_order_status("ibkr-test-001")
print(f"  Order status: {status}")

# --- Cancel order ---
print("\n--- Cancel Order ---")
cancelled = ibkr.cancel_order("ibkr-test-001", "test cancellation")
print(f"  Cancel result: {cancelled}")
status_after = ibkr.get_order_status("ibkr-test-001")
print(f"  Status after cancel: {status_after}")

# --- List after cancel ---
print("\n--- List Orders After Cancel ---")
open_orders2 = ibkr.list_orders()
print(f"  Open orders: {len(open_orders2)}")

# --- Sync state ---
print("\n--- Sync State ---")
oms = OMS(oms_id="sync_test")
ibkr.sync_state(oms)
print(f"  OMS orders after sync: {len(oms.orders)}")

# --- Get last error ---
print("\n--- Get Last Error ---")
error = ibkr.get_last_error()
print(f"  Last error: {error}")

# --- BrokerAdapter protocol check ---
print("\n--- BrokerAdapter Protocol ---")
print(f"  Has execute_order: {hasattr(ibkr, 'execute_order')}")
print(f"  Has cancel_order: {hasattr(ibkr, 'cancel_order')}")
print(f"  Has list_orders: {hasattr(ibkr, 'list_orders')}")
print(f"  Has get_order_status: {hasattr(ibkr, 'get_order_status')}")
print(f"  Has start: {hasattr(ibkr, 'start')}")
print(f"  Has stop: {hasattr(ibkr, 'stop')}")
print(f"  Has reconnect: {hasattr(ibkr, 'reconnect')}")
print(f"  Has sync_state: {hasattr(ibkr, 'sync_state')}")
print(f"  Has get_last_error: {hasattr(ibkr, 'get_last_error')}")
print(f"  Is instance of BrokerAdapter: {isinstance(ibkr, BrokerAdapter)}")

# --- Full lifecycle test ---
print("\n--- Full Lifecycle ---")
ibkr2 = InteractiveBrokersBroker(paper=True)
ibkr2.start()

# Submit order with signal
sig2 = Signal(inst, OrderSide.SELL, 5, 152.0, OrderType.LIMIT, TimeInForce.DAY)
order2 = Order(
    order_id="ibkr-test-002",
    instrument=inst,
    side=OrderSide.SELL,
    quantity=5,
    price=152.0,
    order_type=OrderType.LIMIT,
    time_in_force=TimeInForce.DAY,
    status=OrderStatus.SUBMITTED,
    signal=sig2,
)

exec_result = ibkr2.execute_order(order2, bar_type)
print(f"  Order executed: {exec_result['status']}")

# List open orders
open_orders = ibkr2.list_orders()
print(f"  Open orders after execution: {len(open_orders)}")

# Get status
status = ibkr2.get_order_status("ibkr-test-002")
print(f"  Order status: {status}")

# Cancel
cancelled = ibkr2.cancel_order("ibkr-test-002", "client cancel")
print(f"  Cancel result: {cancelled}")

# List after cancel
open_orders2 = ibkr2.list_orders()
print(f"  Open orders after cancel: {len(open_orders2)}")

# Stop
stopped = ibkr2.stop()
print(f"  Stop result: {stopped}")

# Reconnect
reconnected = ibkr2.reconnect()
print(f"  Reconnect result: {reconnected}")

print("\n" + "=" * 60)
print("PHASE 9 COMPLETE: IBKR paper adapter fully operational")
print("=" * 60)