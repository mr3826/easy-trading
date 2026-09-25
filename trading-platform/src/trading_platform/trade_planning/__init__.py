"""Signal evidence, trade plans, and risk-based position sizing.

Layer between strategy output and the hard risk engine:

    strategy -> SignalEvidence -> TradePlan -> HardRiskEngine -> OMS

ELIGIBILITY (deterministic rules) is strictly separated from RANKING
(signal_score). A score can order otherwise-valid candidates; it can never
make an ineligible candidate eligible.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Mapping, Optional, Tuple

TRADE_PLANNING_VERSION = "1.0.0"


class TradePlanningError(ValueError):
    """Raised for invalid evidence/plan/sizing input."""


# ---------------------------------------------------------------------------
# Signal evidence


@dataclass(frozen=True)
class SignalEvidence:
    """Deterministic, fully-provenanced record of a candidate signal."""

    symbol: str
    decision_timestamp: str  # ISO-8601 UTC
    strategy_id: str
    strategy_version: str
    market_regime: Mapping[str, Any]
    raw_signal: str  # "BUY" | "SELL" | "HOLD"
    trend_confirmation: bool
    relative_strength: float
    volatility_state: str
    liquidity_state: str
    volume_confirmation: bool
    feature_snapshot_hash: str
    expected_entry: float
    initial_stop: float
    planned_exit: str  # e.g. "atr_trailing" | "opposite_signal" | "donchian"
    estimated_transaction_cost: float
    signal_score: float  # ranking only — never confers eligibility
    rejection_reasons: Tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        """A signal is eligible iff no rejection reasons were recorded."""
        return not self.rejection_reasons and self.raw_signal in ("BUY", "SELL")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "decision_timestamp": self.decision_timestamp,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "market_regime": dict(self.market_regime),
            "raw_signal": self.raw_signal,
            "trend_confirmation": self.trend_confirmation,
            "relative_strength": self.relative_strength,
            "volatility_state": self.volatility_state,
            "liquidity_state": self.liquidity_state,
            "volume_confirmation": self.volume_confirmation,
            "feature_snapshot_hash": self.feature_snapshot_hash,
            "expected_entry": self.expected_entry,
            "initial_stop": self.initial_stop,
            "planned_exit": self.planned_exit,
            "estimated_transaction_cost": self.estimated_transaction_cost,
            "signal_score": self.signal_score,
            "rejection_reasons": list(self.rejection_reasons),
            "eligible": self.eligible,
        }

    def evidence_id(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, default=str).encode("utf-8")).hexdigest()


def rank_evidence(candidates: List[SignalEvidence]) -> List[SignalEvidence]:
    """Order ELIGIBLE candidates by score; ineligible candidates never rank."""
    eligible = [c for c in candidates if c.eligible]
    ineligible = [c for c in candidates if not c.eligible]
    return sorted(eligible, key=lambda c: (-c.signal_score, c.symbol)) + sorted(ineligible, key=lambda c: c.symbol)


# ---------------------------------------------------------------------------
# Trade plan


@dataclass(frozen=True)
class TradePlan:
    """Fully-specified proposed trade submitted to the hard risk engine."""

    symbol: str
    direction: str  # long-only V1: "LONG"
    strategy_id: str
    strategy_version: str
    decision_timestamp: str
    entry_style: str  # "MARKET_NEXT_OPEN" | "LIMIT"
    estimated_entry: float
    quantity: int
    risk_budget: float
    initial_stop: float
    take_profit: Optional[float]
    trailing_stop: Optional[Mapping[str, Any]]
    max_holding_days: int
    expected_risk_per_share: float
    estimated_fees: float
    estimated_slippage: float
    expected_total_cost: float
    evidence: SignalEvidence
    version: str = TRADE_PLANNING_VERSION

    def __post_init__(self) -> None:
        if self.direction != "LONG":
            raise TradePlanningError("long-only V1: direction must be LONG")
        if self.quantity < 1:
            raise TradePlanningError("quantity must be >= 1")
        if not math.isfinite(self.estimated_entry) or self.estimated_entry <= 0:
            raise TradePlanningError("estimated_entry must be positive and finite")
        if not math.isfinite(self.initial_stop) or self.initial_stop <= 0:
            raise TradePlanningError("initial_stop must be positive and finite")
        if self.initial_stop >= self.estimated_entry:
            raise TradePlanningError("long initial_stop must be below estimated_entry")
        if not math.isfinite(self.expected_risk_per_share) or self.expected_risk_per_share <= 0:
            raise TradePlanningError("expected_risk_per_share must be positive")
        if not self.evidence.eligible:
            raise TradePlanningError("cannot plan a trade from ineligible evidence")

    def plan_id(self) -> str:
        payload = {
            "symbol": self.symbol,
            "direction": self.direction,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "decision_timestamp": self.decision_timestamp,
            "estimated_entry": self.estimated_entry,
            "quantity": self.quantity,
            "initial_stop": self.initial_stop,
            "version": self.version,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def with_quantity(self, quantity: int) -> "TradePlan":
        return replace(self, quantity=quantity)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "decision_timestamp": self.decision_timestamp,
            "entry_style": self.entry_style,
            "estimated_entry": self.estimated_entry,
            "quantity": self.quantity,
            "risk_budget": self.risk_budget,
            "initial_stop": self.initial_stop,
            "take_profit": self.take_profit,
            "trailing_stop": dict(self.trailing_stop) if self.trailing_stop else None,
            "max_holding_days": self.max_holding_days,
            "expected_risk_per_share": self.expected_risk_per_share,
            "estimated_fees": self.estimated_fees,
            "estimated_slippage": self.estimated_slippage,
            "expected_total_cost": self.expected_total_cost,
            "evidence": self.evidence.to_dict(),
            "version": self.version,
            "plan_id": self.plan_id(),
        }


# ---------------------------------------------------------------------------
# Position sizing


@dataclass(frozen=True)
class SizingPolicy:
    """Risk-based sizing configuration. Values are POLICY CHOICES.

    Kelly-style growth-optimal sizing is intentionally unsupported for
    promotion by default; fractional risk-of-ruin sizing is the default.
    """

    risk_fraction: float = 0.01  # fraction of equity risked per trade
    max_position_notional_pct: float = 0.20  # of equity
    max_gross_exposure_pct: float = 0.90
    min_cash_reserve_pct: float = 0.05
    max_positions: int = 3
    max_quantity: int = 100_000
    commission_per_order: float = 1.0
    version: str = TRADE_PLANNING_VERSION

    def __post_init__(self) -> None:
        for name in (
            "risk_fraction",
            "max_position_notional_pct",
            "max_gross_exposure_pct",
            "min_cash_reserve_pct",
        ):
            v = getattr(self, name)
            if not math.isfinite(v) or not 0 < v <= 1:
                raise TradePlanningError(f"{name} must be in (0, 1], got {v!r}")
        if self.max_positions < 1 or self.max_quantity < 1:
            raise TradePlanningError("position/quantity caps must be positive")


@dataclass(frozen=True)
class PortfolioState:
    equity: float
    settled_cash: float
    gross_exposure: float
    open_positions: int
    symbol_sector: str = ""
    sector_exposure: float = 0.0


@dataclass(frozen=True)
class SizingResult:
    quantity: int
    risk_budget: float
    risk_per_share: float
    binding_constraint: str
    rejection_reasons: Tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.quantity >= 1 and not self.rejection_reasons


def size_position(
    evidence: SignalEvidence,
    portfolio: PortfolioState,
    policy: SizingPolicy,
    *,
    sector_cap_pct: Optional[float] = None,
) -> SizingResult:
    """Volatility/risk-based sizing:

        risk_budget  = equity * risk_fraction
        risk/share   = entry - initial_stop
        shares       = floor(risk_budget / risk_per_share)

    then clamped by cash, notional, exposure, sector, position-count and
    hard quantity caps. One-share diagnostic simulations remain possible by
    bypassing this function; production research uses this path.
    """
    if not evidence.eligible:
        return SizingResult(0, 0.0, 0.0, "none", ("evidence ineligible",))
    if portfolio.equity <= 0 or not math.isfinite(portfolio.equity):
        return SizingResult(0, 0.0, 0.0, "none", ("invalid equity",))
    entry = evidence.expected_entry
    stop = evidence.initial_stop
    risk_per_share = entry - stop
    if risk_per_share <= 0 or not math.isfinite(risk_per_share):
        return SizingResult(0, 0.0, 0.0, "none", ("non-positive risk per share",))
    if portfolio.open_positions >= policy.max_positions:
        return SizingResult(0, 0.0, risk_per_share, "max_positions", ("max positions reached",))

    risk_budget = portfolio.equity * policy.risk_fraction
    qty_risk = math.floor(risk_budget / risk_per_share)

    # Cash: entry capital plus reserve plus the entry-side commission.
    cash_available = portfolio.settled_cash - portfolio.equity * policy.min_cash_reserve_pct
    qty_cash = math.floor(max(cash_available - policy.commission_per_order, 0.0) / entry)

    # Notional / exposure caps.
    qty_notional = math.floor(portfolio.equity * policy.max_position_notional_pct / entry)
    exposure_room = portfolio.equity * policy.max_gross_exposure_pct - portfolio.gross_exposure
    qty_exposure = math.floor(max(exposure_room, 0.0) / entry)

    qty_sector = policy.max_quantity
    sector_binding = ""
    if sector_cap_pct is not None and portfolio.symbol_sector:
        sector_room = portfolio.equity * sector_cap_pct - portfolio.sector_exposure
        qty_sector = math.floor(max(sector_room, 0.0) / entry)
        sector_binding = "sector_cap"

    candidates = [
        (qty_risk, "risk_budget"),
        (qty_cash, "settled_cash"),
        (qty_notional, "position_notional"),
        (qty_exposure, "gross_exposure"),
        (qty_sector, sector_binding or "sector_cap"),
        (policy.max_quantity, "hard_quantity_cap"),
    ]
    quantity, binding = min(candidates, key=lambda c: c[0])
    quantity = int(max(quantity, 0))
    if quantity < 1:
        return SizingResult(0, risk_budget, risk_per_share, binding, (f"constraint {binding} binds",))
    return SizingResult(quantity, risk_budget, risk_per_share, binding)


def build_trade_plan(
    evidence: SignalEvidence,
    portfolio: PortfolioState,
    sizing_policy: SizingPolicy,
    *,
    entry_style: str = "MARKET_NEXT_OPEN",
    take_profit: Optional[float] = None,
    trailing_stop: Optional[Mapping[str, Any]] = None,
    max_holding_days: int = 30,
    commission_per_order: float = 1.0,
    slippage_pct: float = 0.001,
    sector_cap_pct: Optional[float] = None,
) -> Tuple[Optional[TradePlan], Tuple[str, ...]]:
    """Size an eligible signal and construct a TradePlan.

    Returns (plan, rejection_reasons); exactly one is non-empty.
    """
    sizing = size_position(evidence, portfolio, sizing_policy, sector_cap_pct=sector_cap_pct)
    if not sizing.accepted:
        return None, sizing.rejection_reasons
    entry = evidence.expected_entry
    fees = commission_per_order * 2  # round trip estimate
    slippage = entry * sizing.quantity * slippage_pct * 2
    plan = TradePlan(
        symbol=evidence.symbol,
        direction="LONG",
        strategy_id=evidence.strategy_id,
        strategy_version=evidence.strategy_version,
        decision_timestamp=evidence.decision_timestamp,
        entry_style=entry_style,
        estimated_entry=entry,
        quantity=sizing.quantity,
        risk_budget=sizing.risk_budget,
        initial_stop=evidence.initial_stop,
        take_profit=take_profit,
        trailing_stop=trailing_stop,
        max_holding_days=max_holding_days,
        expected_risk_per_share=sizing.risk_per_share,
        estimated_fees=fees,
        estimated_slippage=slippage,
        expected_total_cost=fees + slippage,
        evidence=evidence,
    )
    return plan, ()
