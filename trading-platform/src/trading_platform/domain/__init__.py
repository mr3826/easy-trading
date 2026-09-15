from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Dict, Optional


class InstrumentType(Enum):
    STOCK = auto()
    ETF = auto()
    INDEX = auto()


@dataclass(frozen=False)
class Instrument:
    symbol: str
    instrument_type: InstrumentType = InstrumentType.STOCK
    exchange: str = "SMART"
    currency: str = "USD"

    def __hash__(self) -> int:
        return hash(self.symbol)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Instrument):
            return NotImplemented
        return self.symbol == other.symbol and self.instrument_type == other.instrument_type


class TradingSession(Enum):
    DAY = auto()
    OVERNIGHT = auto()


@dataclass(frozen=False)
class Bar:
    instrument: Instrument
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    session: TradingSession = TradingSession.DAY

    @property
    def bar_date(self) -> str:
        return self.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d")


@dataclass(frozen=False)
class CorporateAction:
    instrument: Instrument
    action_type: str  # "split", "dividend", "reverse_split", "delisting"
    ex_date: datetime
    record_date: datetime | None = None
    pay_date: datetime | None = None
    ratio: float | None = None  # e.g., 2.0 for 2-for-1 split
    cash_amount: float | None = None  # dividend cash per share


# ---- Order-related types ----

class OrderSide(Enum):
    BUY = auto()
    SELL = auto()


class OrderType(Enum):
    MARKET = auto()
    LIMIT = auto()
    STOP = auto()
    STOP_LIMIT = auto()


class TimeInForce(Enum):
    DAY = auto()
    GTC = auto()  # Good 'Til Cancelled
    IOC = auto()  # Immediate or Cancel
    FOK = auto()  # Fill or Kill


@dataclass(frozen=False)
class Signal:
    instrument: Instrument
    side: OrderSide
    quantity: int
    price: Optional[float]  # None for market orders
    order_type: OrderType
    time_in_force: TimeInForce
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=False)
class OrderIntent:
    signal: Signal
    order_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=False)
class RiskDecision:
    order_intent: OrderIntent
    approved: bool
    reason: str | None = None
    position_notional: float | None = None
    risk_violation: str | None = None
    approved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=False)
class Order:
    order_id: str
    instrument: Instrument
    side: OrderSide
    quantity: int
    price: Optional[float]
    order_type: OrderType
    time_in_force: TimeInForce
    status: OrderLifecycle
    signal: Signal
    risk_decision: RiskDecision | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    filled_at: datetime | None = None
    filled_price: float | None = None
    filled_quantity: int | None = None
    average_fill_price: float | None = None


class OrderStatus(Enum):
    SUBMITTED = auto()
    RECEIVED = auto()
    PARTIALLY_FILLED = auto()
    FILLED = auto()
    CANCELLED = auto()
    REJECTED = auto()
    EXPIRED = auto()


@dataclass(frozen=False)
class OrderLeg:
    leg_id: str
    order: Order
    parent_order_id: str | None = None


@dataclass(frozen=False)
class Execution:
    execution_id: str
    order_id: str
    instrument: Instrument
    side: OrderSide
    quantity: int
    price: float
    timestamp: datetime
    broker_order_id: str | None = None
    trade_id: str | None = None


@dataclass(frozen=False)
class Position:
    instrument: Instrument
    quantity: int
    average_cost: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float
    last_update: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.average_cost


@dataclass(frozen=False)
class PortfolioSnapshot:
    timestamp: datetime
    cash: float
    positions: Dict[Instrument, Position]
    gross_exposure: float
    net_exposure: float
    total_pnl: float


@dataclass(frozen=False)
class BrokerSnapshot:
    timestamp: datetime
    positions: Dict[Instrument, float]
    cash: float
    buying_power: float
    margin_used: float | None = None
    status: str = "connected"


@dataclass(frozen=False)
class ReconciliationResult:
    timestamp: datetime
    differences: Dict[str, Any]
    cash_overview: Dict[str, Any]
    position_overview: Dict[str, Any]
    order_overview: Dict[str, Any]
    fill_overview: Dict[str, Any]
    reconciled: bool
    blocks_submissions: bool


@dataclass(frozen=False)
class JournalEvent:
    event_id: str
    timestamp: datetime
    environment: str  # "research", "simulation", "shadow", "paper", "live"
    code_version: str
    config_version: str
    event_type: str
    payload: Dict[str, Any]
    source: str
    checksum: str

    @property
    def event_date(self) -> str:
        return self.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")