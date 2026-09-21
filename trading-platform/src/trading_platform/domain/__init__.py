from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Dict, Optional


class InstrumentType(Enum):
    STOCK = auto()
    ETF = auto()
    INDEX = auto()


@dataclass(frozen=True)
class Instrument:
    symbol: str
    instrument_type: InstrumentType = InstrumentType.STOCK
    exchange: str = "SMART"
    currency: str = "USD"

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        if not self.symbol or any(ch.isspace() for ch in self.symbol):
            raise ValueError("instrument symbol must be non-empty and contain no spaces")

    def __hash__(self) -> int:
        return hash(self.symbol)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Instrument):
            return NotImplemented
        return self.symbol == other.symbol and self.instrument_type == other.instrument_type


class TradingSession(Enum):
    DAY = auto()
    OVERNIGHT = auto()


@dataclass(frozen=True)
class Bar:
    instrument: Instrument
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    session: TradingSession = TradingSession.DAY
    available_at: datetime | None = None

    def __post_init__(self) -> None:
        require_utc(self.timestamp)
        if self.available_at is not None:
            require_utc(self.available_at)
        prices = (self.open, self.high, self.low, self.close)
        if any(not math.isfinite(price) or price <= 0 for price in prices):
            raise ValueError("bar prices must be finite and positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("bar OHLC values are inconsistent")
        if self.volume < 0:
            raise ValueError("bar volume cannot be negative")

    @property
    def bar_date(self) -> str:
        return self.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d")


class CorporateActionType(Enum):
    SPLIT = "split"
    DIVIDEND = "dividend"
    REVERSE_SPLIT = "reverse_split"
    DELISTING = "delisting"


@dataclass(frozen=True)
class CorporateAction:
    instrument: Instrument
    action_type: CorporateActionType
    ex_date: datetime
    record_date: datetime | None = None
    pay_date: datetime | None = None
    ratio: float | None = None  # e.g., 2.0 for 2-for-1 split
    cash_amount: float | None = None  # dividend cash per share

    def __post_init__(self) -> None:
        require_utc(self.ex_date)
        if self.action_type in (CorporateActionType.SPLIT, CorporateActionType.REVERSE_SPLIT):
            if self.ratio is None or not math.isfinite(self.ratio) or self.ratio <= 0:
                raise ValueError("split corporate actions require a positive finite ratio")
        if self.action_type == CorporateActionType.DIVIDEND:
            if self.cash_amount is None or not math.isfinite(self.cash_amount) or self.cash_amount <= 0:
                raise ValueError("dividend corporate actions require a positive finite cash amount")


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


@dataclass(frozen=True)
class Signal:
    instrument: Instrument
    side: OrderSide
    quantity: int
    price: Optional[float]  # None for market orders
    order_type: OrderType
    time_in_force: TimeInForce
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("signal quantity must be positive")
        if self.price is not None and (not math.isfinite(self.price) or self.price <= 0):
            raise ValueError("signal price must be finite and positive")


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
    status: OrderStatus
    signal: Signal | None
    risk_decision: RiskDecision | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    filled_at: datetime | None = None
    filled_price: float | None = None
    filled_quantity: int | None = None
    average_fill_price: float | None = None

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("order quantity must be positive")
        if self.price is not None and (not math.isfinite(self.price) or self.price <= 0):
            raise ValueError("order price must be finite and positive")
        require_utc(self.created_at)


class OrderStatus(Enum):
    SUBMITTED = auto()
    RECEIVED = auto()
    ACCEPTED = auto()
    OPEN = auto()
    PARTIALLY_FILLED = auto()
    FILLED = auto()
    CANCELLED = auto()
    REJECTED = auto()
    EXPIRED = auto()
    # Spelling alias used by the OMS lifecycle.
    CANCELED = CANCELLED


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

    def __post_init__(self) -> None:
        if not math.isfinite(self.average_cost) or self.average_cost < 0:
            raise ValueError("position average cost must be finite and non-negative")
        if any(not math.isfinite(value) for value in (self.market_value, self.unrealized_pnl, self.realized_pnl)):
            raise ValueError("position financial values must be finite")
        self.last_update = _coerce_utc(self.last_update)

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.average_cost


@dataclass(frozen=True)
class PortfolioSnapshot:
    timestamp: datetime
    cash: float
    positions: Dict[str, Position]
    gross_exposure: float
    net_exposure: float
    total_pnl: float


@dataclass(frozen=True)
class BrokerSnapshot:
    timestamp: datetime
    positions: Dict[Instrument, float]
    cash: float
    buying_power: float
    margin_used: float | None = None
    status: str = "connected"


@dataclass(frozen=True)
class ReconciliationResult:
    timestamp: datetime
    differences: Dict[str, Any]
    cash_overview: Dict[str, Any]
    position_overview: Dict[str, Any]
    order_overview: Dict[str, Any]
    fill_overview: Dict[str, Any]
    reconciled: bool
    blocks_submissions: bool


@dataclass(frozen=True)
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

    def __post_init__(self) -> None:
        require_utc(self.timestamp)

    @property
    def event_date(self) -> str:
        return self.timestamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")


def _coerce_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


_SENSITIVE_KEYS = frozenset(
    {"api_key", "apikey", "secret", "password", "token", "access_token", "refresh_token", "private_key", "credentials"}
)
_REDACTED_VALUE = "***REDACTED***"


def redact_secrets(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of payload with values of sensitive keys masked."""
    return {key: (_REDACTED_VALUE if key.lower() in _SENSITIVE_KEYS else value) for key, value in payload.items()}
