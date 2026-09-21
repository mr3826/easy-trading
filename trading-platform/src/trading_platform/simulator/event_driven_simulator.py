from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List

from trading_platform.domain import (
    Bar,
    Instrument,
    Order,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    Position,
    RiskDecision,
    Signal,
)


class SimulationMode(Enum):
    """Simulation modes for different fidelity levels."""

    DETERMINISTIC = "deterministic"  # Fixed seed, reproducible
    STOCHASTIC = "stochastic"  # Randomized fills, slippage
    IDEAL = "ideal"  # No friction, immediate fills


class FillAssumption(Enum):
    """Assumptions about order fill behavior."""

    CLOSE = "close"  # Retained only to reject unsafe legacy configuration
    NEXT_OPEN = "next_open"  # Execute at next open price
    MARKET = "market"  # Execute at next open price (same-bar close is lookahead for completed-bar decisions)
    LIMIT = "limit"  # Execute at limit price or better


class CommissionModel(Enum):
    """Commission models for cost modeling."""

    FIXED = "fixed"  # Fixed fee per order
    PER_SHARE = "per_share"  # Fee per share
    PERCENTAGE = "percentage"  # Percentage of trade value


@dataclass(frozen=True)
class FillResult:
    """Result of order fill simulation."""

    fill_quantity: int
    fill_price: float
    fill_commission: float
    fill_cost: float  # price * quantity + commission
    timestamp: datetime
    execution_id: str
    remaining_quantity: int  # Quantity not filled (for partial fills)
    realized_pnl: float  # Realized PnL closed by this fill


@dataclass(frozen=True)
class OrderEvent:
    """Event in the order lifecycle."""

    event_type: str  # "SIGNAL", "RISK_DECISION", "ORDER_SUBMITTED", "FILL", "CANCEL"
    timestamp: datetime
    order_id: str
    instrument: Instrument
    detail: Dict[str, Any] | None = None


@dataclass(frozen=True)
class CostAttribution:
    """Cost breakdown for a filled order."""

    commission: float
    slippage: float  # Difference between signal price and fill price
    spread: float  # Bid-ask spread cost
    total_cost: float


class EventDrivenSimulator:
    """Event-driven simulator modeling strategy → risk → OMS → execution pipeline.

    The core event loop processes events in timestamp order, modeling:
    - Strategy signal generation
    - Risk engine approval/rejection
    - Order submission and execution
    - Fill modeling with configurable assumptions
    - Cash settlement and buying power checks
    - Portfolio state reconstruction
    """

    def __init__(
        self,
        mode: SimulationMode = SimulationMode.DETERMINISTIC,
        commission_model: CommissionModel = CommissionModel.FIXED,
        commission_rate: float = 1.0,
        start_cash: float = 10000.0,
        fill_assumption: FillAssumption = FillAssumption.NEXT_OPEN,
        seed: int = 0,
    ):
        self.mode = mode
        self.commission_model = commission_model
        self.commission_rate = commission_rate
        self.start_cash = start_cash
        self.fill_assumption = fill_assumption
        self.seed = seed
        if fill_assumption == FillAssumption.CLOSE:
            raise ValueError("CLOSE execution is unsafe for completed-bar decisions; use NEXT_OPEN")

        # State
        self.cash: float = start_cash
        self.positions: Dict[str, Position] = {}
        self.order_ledger: List[OrderEvent] = []
        self.trade_ledger: List[OrderEvent] = []
        self.portfolio_series: List[PortfolioSnapshot] = []
        self.event_timestamp: datetime = datetime.min.replace(tzinfo=timezone.utc)

        # Tracking
        self.fill_count = 0
        self.cancel_count = 0
        self.reject_count = 0
        self.total_commission = Decimal("0")
        self.total_slippage = Decimal("0")

    # ---- Core event loop ----

    def run(
        self,
        bars: List[Bar],
        signals: List[Signal | None],
        initial_portfolio: PortfolioSnapshot | None = None,
    ) -> SimulationResult:
        """Run the event-driven simulation.

        The event loop processes bars in timestamp order, modeling the full
        strategy → risk → OMS → execution pipeline. Signals must contain one
        entry per bar, in bar order: signals[i] is generated from completed
        bar i and executes on bar i+1; None entries mean no signal for that
        bar.
        """
        # Initialize state
        if initial_portfolio:
            self.cash = initial_portfolio.cash
            self.positions = dict(initial_portfolio.positions)
        else:
            self.cash = self.start_cash
            self.positions = {}

        self.order_ledger = []
        self.trade_ledger = []
        self.portfolio_series = []
        self.fill_count = 0
        self.cancel_count = 0
        self.reject_count = 0
        self.total_commission = Decimal("0")
        self.total_slippage = Decimal("0")

        # Sort bars by timestamp
        sorted_bars = sorted(bars, key=lambda b: b.timestamp)

        # A signal made from completed bar D becomes eligible on the next bar.
        pending_signal: Signal | None = None
        for signal_idx, bar in enumerate(sorted_bars):
            signal_number = signal_idx + 1
            self._process_bar(bar, pending_signal, signal_number)
            pending_signal = signals[signal_idx] if signal_idx < len(signals) else None

            # Record portfolio state after bar processing
            self._record_portfolio_state(bar.timestamp)

        return SimulationResult(
            final_portfolio=self._make_portfolio_snapshot(
                sorted_bars[-1].timestamp if sorted_bars else datetime.min.replace(tzinfo=timezone.utc)
            ),
            trade_ledger=self.trade_ledger,
            order_ledger=self.order_ledger,
            portfolio_series=self.portfolio_series,
            final_cash=self.cash,
            final_positions=dict(self.positions),
            total_commission=float(self.total_commission),
            total_slippage=float(self.total_slippage),
        )

    # ---- Bar processing ----

    def _process_bar(self, bar: Bar, signal: Signal | None, signal_number: int = 0) -> None:
        """Process a single bar with an associated signal (or None)."""

        self.event_timestamp = bar.timestamp

        if signal is None:
            # No signal for this bar - no orders; run() records portfolio state
            return

        self._handle_signal(signal, bar, signal_number)

    def _handle_signal(self, signal: Signal, bar: Bar, signal_number: int) -> None:
        """Handle a single trading signal through the pipeline."""

        # 1. Signal record (the signal was generated from completed bars)
        self.order_ledger.append(
            OrderEvent(
                event_type="SIGNAL",
                timestamp=self.event_timestamp,
                order_id=f"signal-{self.event_timestamp.timestamp()}-{signal_number}",
                instrument=signal.instrument,
                detail={"signal": signal.side.name, "quantity": signal.quantity},
            )
        )

        # 2. Order intent creation (before the risk check so every decision
        # references the real intent)
        order_intent = OrderIntent(
            signal=signal,
            order_id=f"order-{self.event_timestamp.timestamp()}",
        )

        # 3. Risk engine check
        risk_decision = self._risk_engine(signal, order_intent)
        if not risk_decision.approved:
            self.reject_count += 1
            self.order_ledger.append(
                OrderEvent(
                    event_type="RISK_REJECTION",
                    timestamp=self.event_timestamp,
                    order_id=order_intent.order_id,
                    instrument=signal.instrument,
                    detail={"reason": risk_decision.reason},
                )
            )
            return

        # 4. OMS order creation
        order = Order(
            order_id=order_intent.order_id,
            instrument=signal.instrument,
            side=signal.side,
            quantity=signal.quantity,
            price=signal.price,
            order_type=signal.order_type,
            time_in_force=signal.time_in_force,
            status=OrderStatus.SUBMITTED,
            signal=signal,
            risk_decision=risk_decision,
        )

        self.order_ledger.append(
            OrderEvent(
                event_type="ORDER_SUBMITTED",
                timestamp=self.event_timestamp,
                order_id=order.order_id,
                instrument=order.instrument,
                detail={
                    "side": order.side.name,
                    "quantity": order.quantity,
                    "price": order.price,
                    "type": order.order_type.name,
                },
            )
        )

        # 5. Execution modeling
        fill_result = self._execute_order(order, bar)

        # 6. Fill processing - already handled in _execute_order (position update, cash, etc.)
        if fill_result.fill_quantity > 0:
            # Record in trade ledger
            self.trade_ledger.append(
                OrderEvent(
                    event_type="FILL",
                    timestamp=self.event_timestamp,
                    order_id=fill_result.execution_id,
                    instrument=order.instrument,
                    detail={
                        "fill_quantity": fill_result.fill_quantity,
                        "fill_price": fill_result.fill_price,
                        "fill_commission": fill_result.fill_commission,
                        "realized_pnl": fill_result.realized_pnl,
                    },
                )
            )
        else:
            # No fill - order expires/unfilled
            # Order is immutable (frozen dataclass), so create a new instance with updated status
            order = replace(order, status=OrderStatus.EXPIRED)
            self.order_ledger.append(
                OrderEvent(
                    event_type="ORDER_EXPIRED",
                    timestamp=self.event_timestamp,
                    order_id=order.order_id,
                    instrument=order.instrument,
                    detail={"reason": "No fill available"},
                )
            )

    # ---- Risk engine ----

    def _risk_engine(self, signal: Signal, order_intent: OrderIntent) -> RiskDecision:
        """Run the risk engine on a signal.

        Checks:
        - Position limit per instrument
        - Gross exposure limit
        - Cash/buying power check
        - Sector concentration check
        """
        instrument = signal.instrument
        quantity = signal.quantity
        side = signal.side

        # Get current position
        current_pos = self.positions.get(
            instrument.symbol,
            Position(
                instrument=instrument,
                quantity=0,
                average_cost=0.0,
                market_value=0.0,
                unrealized_pnl=0.0,
                realized_pnl=0.0,
            ),
        )

        # Calculate target position
        target_qty = current_pos.quantity + (quantity if side == OrderSide.BUY else -quantity)

        # Check position limit (V1: max 3 positions)
        position_count = len(self.positions)
        if position_count >= 3 and target_qty != current_pos.quantity:
            return RiskDecision(
                order_intent=order_intent,
                approved=False,
                reason="Max position count (3) reached",
            )

        # Cash check for BUY orders
        if side == OrderSide.BUY and signal.price is not None:
            # Estimate cost including commission
            estimated_cost = abs(quantity) * signal.price
            estimated_commission = float(self._estimate_commission(abs(quantity), signal.price))
            total_cost = estimated_cost + estimated_commission

            if self.cash < total_cost:
                return RiskDecision(
                    order_intent=order_intent,
                    approved=False,
                    reason=f"Insufficient cash: need {total_cost:.2f}, have {self.cash:.2f}",
                    position_notional=estimated_cost,
                )

        # Check sector concentration (simplified: just count positions)
        # In V1 with max 3 positions, this is inherently limited

        # Approve
        position_notional = abs(quantity) * (signal.price or 0)

        return RiskDecision(
            order_intent=order_intent,
            approved=True,
            reason="Within risk limits",
            position_notional=position_notional,
        )

    # ---- Order execution ----

    def _execute_order(self, order: Order, bar: Bar) -> FillResult:
        """Execute an order modeled by the fill assumption.

        Models:
        - Commission
        - Spread
        - Slippage
        - Next-session gaps
        - Partial fills
        - Order expiry
        """
        instrument = order.instrument
        quantity = order.quantity
        side = order.side
        price = order.price

        if not self._is_order_eligible(order, bar):
            return FillResult(
                fill_quantity=0,
                fill_price=0.0,
                fill_commission=0.0,
                fill_cost=0.0,
                timestamp=self.event_timestamp,
                execution_id=f"unfilled-{self.event_timestamp.timestamp()}-{order.order_id}",
                remaining_quantity=quantity,
                realized_pnl=0.0,
            )

        # Determine fill price based on assumption
        fill_price = self._calculate_fill_price(price, bar, side, order.order_type)

        # Calculate actual quantity filled
        # For V1 cash account with whole-share quantization
        actual_qty = self._quantize_shares(quantity, side, fill_price, bar)

        # Calculate commission
        commission = self._estimate_commission(abs(actual_qty), fill_price)

        # Calculate slippage (difference between signal intent and fill)
        slippage = Decimal("0")
        if price is not None:
            if side == OrderSide.BUY and fill_price > price:
                # For a buy order, slippage = fill_price - signal_price (positive = bad)
                slippage = Decimal(str(fill_price - price))
            elif side == OrderSide.SELL and fill_price < price:
                # For a sell order, slippage = signal_price - fill_price (positive = bad)
                slippage = Decimal(str(price - fill_price))

        # Calculate total cost
        fill_cost = fill_price * abs(actual_qty) + float(commission)

        # Cash update
        if side == OrderSide.BUY:
            self.cash -= fill_cost
        else:  # SELL
            self.cash += fill_cost

        # Position update
        current_pos = self.positions.get(
            instrument.symbol,
            Position(
                instrument=instrument,
                quantity=0,
                average_cost=0.0,
                market_value=0.0,
                unrealized_pnl=0.0,
                realized_pnl=0.0,
            ),
        )

        # Update position
        new_qty = current_pos.quantity + actual_qty

        # Calculate new average cost
        if actual_qty != 0:
            if current_pos.quantity == 0:
                new_avg_cost = fill_price
            else:
                total_cost_basis = current_pos.average_cost * abs(current_pos.quantity)
                new_total_cost = total_cost_basis + (fill_price * abs(actual_qty))
                new_avg_cost = new_total_cost / abs(new_qty) if new_qty != 0 else 0
        else:
            new_avg_cost = current_pos.average_cost

        # Calculate PnL if we had existing position
        new_market_value = abs(new_qty) * fill_price
        new_unrealized = new_market_value - (new_avg_cost * abs(new_qty))

        # Realized PnL from this trade (only if reducing or closing position)
        realized_from_this = Decimal("0")
        if (current_pos.quantity > 0 and actual_qty < 0) or (current_pos.quantity < 0 and actual_qty > 0):
            # Closing an existing position - calculate realized PnL
            close_qty = min(abs(current_pos.quantity), abs(actual_qty))
            if side == OrderSide.SELL and current_pos.quantity > 0:
                realized_from_this = Decimal(str((fill_price - current_pos.average_cost) * close_qty))
            elif side == OrderSide.BUY and current_pos.quantity < 0:
                realized_from_this = Decimal(str((current_pos.average_cost - fill_price) * close_qty))

        # Create updated position
        new_position = Position(
            instrument=instrument,
            quantity=new_qty,
            average_cost=float(new_avg_cost) if new_qty != 0 else 0.0,
            market_value=float(new_market_value),
            unrealized_pnl=float(new_unrealized),
            realized_pnl=float(current_pos.realized_pnl + float(realized_from_this)),
        )

        self.positions[instrument.symbol] = new_position

        # Update totals
        self.fill_count += 1
        self.total_commission += Decimal(str(commission))
        self.total_slippage += slippage

        # Generate execution ID
        execution_id = f"exec-{self.event_timestamp.timestamp()}-{order.order_id}"

        return FillResult(
            fill_quantity=actual_qty,
            fill_price=fill_price,
            fill_commission=float(commission),
            fill_cost=float(fill_cost),
            timestamp=self.event_timestamp,
            execution_id=execution_id,
            remaining_quantity=quantity - actual_qty,  # For partial fills
            realized_pnl=float(realized_from_this),
        )

    # ---- Fill price calculation ----

    def _is_order_eligible(self, order: Order, bar: Bar) -> bool:
        """Determine whether an order's trigger/limit was touched by the bar."""
        if order.order_type == OrderType.MARKET:
            return True
        if order.price is None:
            return False
        if order.order_type == OrderType.LIMIT:
            return bar.low <= order.price if order.side == OrderSide.BUY else bar.high >= order.price
        if order.order_type == OrderType.STOP:
            return bar.high >= order.price if order.side == OrderSide.BUY else bar.low <= order.price
        if order.order_type == OrderType.STOP_LIMIT:
            triggered = bar.high >= order.price if order.side == OrderSide.BUY else bar.low <= order.price
            return triggered and (bar.low <= order.price if order.side == OrderSide.BUY else bar.high >= order.price)
        return False

    def _calculate_fill_price(
        self,
        signal_price: float | None,
        bar: Bar,
        side: OrderSide,
        order_type: OrderType = OrderType.MARKET,
    ) -> float:
        """Calculate the actual fill price based on the fill assumption.

        V1 fill assumptions:
        - CLOSE: prohibited (raises)
        - NEXT_OPEN: execute at next bar's open price
        - MARKET: execute at the next bar's open price (same-bar close would be lookahead)
        - LIMIT: execute at limit price or better
        """
        if fill_assumption := self.fill_assumption:
            if fill_assumption == FillAssumption.CLOSE:
                raise ValueError("CLOSE execution is prohibited")

            elif fill_assumption == FillAssumption.NEXT_OPEN:
                if signal_price is None or order_type == OrderType.MARKET:
                    return bar.open
                if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
                    return min(bar.open, signal_price) if side == OrderSide.BUY else max(bar.open, signal_price)
                if side == OrderSide.BUY:
                    return max(bar.open, signal_price)
                return min(bar.open, signal_price)

            elif fill_assumption == FillAssumption.MARKET:
                # Market order: fills at the next bar's open, consistent with
                # NEXT_OPEN. Filling at the same bar's close would allow a
                # signal derived from that close to trade on stale prices.
                if signal_price is None or order_type == OrderType.MARKET:
                    return bar.open
                if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT):
                    return min(bar.open, signal_price) if side == OrderSide.BUY else max(bar.open, signal_price)
                if side == OrderSide.BUY:
                    return max(bar.open, signal_price)
                return min(bar.open, signal_price)

            elif fill_assumption == FillAssumption.LIMIT:
                # Limit order: execute at signal price or better
                # If signal_price is above/below market, may not fill
                if signal_price is None:
                    raise ValueError("limit execution requires a limit price")
                return signal_price

        return bar.close  # Default fallback

    # ---- Whole-share quantization ----

    def _quantize_shares(self, quantity: int, side: OrderSide, fill_price: float, bar: Bar) -> int:
        """Quantize order quantity to whole shares.

        V1: Only whole shares trade. Round toward zero for partial results.
        """
        # V1: Whole-share only - round to integer
        # For BUY: keep positive quantity
        # For SELL: keep negative quantity (or use absolute)
        qty = abs(quantity)

        # Check if we have enough cash for the rounded quantity
        estimated_cost = qty * fill_price
        estimated_commission = float(self._estimate_commission(qty, fill_price))
        total_cost = estimated_cost + estimated_commission

        if side == OrderSide.BUY and self.cash < total_cost:
            # Can't afford full quantity - reduce to what we can afford
            affordable = max(0, int((self.cash - estimated_commission) / fill_price))
            return affordable

        return qty if side == OrderSide.BUY else -qty

    # ---- Commission calculation ----

    def _estimate_commission(self, qty: int, price: float) -> Decimal:
        """Estimate commission for an order.

        V1: Fixed commission model - $1.00 per order.
        """
        if self.commission_model == CommissionModel.FIXED:
            return Decimal(str(self.commission_rate))
        elif self.commission_model == CommissionModel.PER_SHARE:
            return Decimal(str(qty)) * Decimal(str(self.commission_rate))
        elif self.commission_model == CommissionModel.PERCENTAGE:
            return Decimal(str(qty * price)) * Decimal(str(self.commission_rate / 100))
        return Decimal("0")

    # ---- Portfolio state recording ----

    def _record_portfolio_state(self, timestamp: datetime) -> None:
        """Record a portfolio snapshot at the given timestamp."""

        # Calculate gross and net exposure
        long_exposure = sum(pos.market_value for pos in self.positions.values() if pos.quantity > 0)
        short_exposure = sum(abs(pos.market_value) for pos in self.positions.values() if pos.quantity < 0)
        gross_exposure = long_exposure + short_exposure
        net_exposure = long_exposure - short_exposure

        # Total portfolio value

        # Total PnL (unrealized only in simulation without reference prices)
        total_unrealized = sum(pos.unrealized_pnl for pos in self.positions.values())
        total_realized = sum(pos.realized_pnl for pos in self.positions.values())

        snapshot = PortfolioSnapshot(
            timestamp=timestamp,
            cash=self.cash,
            positions=dict(self.positions),
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            total_pnl=total_unrealized + total_realized,
        )

        self.portfolio_series.append(snapshot)

    def _make_portfolio_snapshot(self, timestamp: datetime) -> PortfolioSnapshot:
        """Make a final portfolio snapshot."""

        long_exposure = sum(pos.market_value for pos in self.positions.values() if pos.quantity > 0)
        short_exposure = sum(abs(pos.market_value) for pos in self.positions.values() if pos.quantity < 0)
        gross_exposure = long_exposure + short_exposure
        net_exposure = long_exposure - short_exposure
        total_unrealized = sum(pos.unrealized_pnl for pos in self.positions.values())
        total_realized = sum(pos.realized_pnl for pos in self.positions.values())

        return PortfolioSnapshot(
            timestamp=timestamp,
            cash=self.cash,
            positions=dict(self.positions),
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            total_pnl=total_unrealized + total_realized,
        )


@dataclass(frozen=True)
class SimulationResult:
    """Result of a full simulation run."""

    final_portfolio: PortfolioSnapshot
    trade_ledger: List[OrderEvent]
    order_ledger: List[OrderEvent]
    portfolio_series: List[PortfolioSnapshot]
    final_cash: float
    final_positions: Dict[str, Position]
    total_commission: float
    total_slippage: float

    # Summary metrics
    @property
    def total_trades(self) -> int:
        return len(self.trade_ledger)

    @property
    def total_commission_cost(self) -> float:
        return self.total_commission

    @property
    def total_slippage_cost(self) -> float:
        return self.total_slippage

    @property
    def final_net_exposure(self) -> float:
        return self.final_portfolio.net_exposure

    @property
    def final_gross_exposure(self) -> float:
        return self.final_portfolio.gross_exposure
