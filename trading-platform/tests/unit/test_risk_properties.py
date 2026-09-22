"""Property-based tests for hard risk limits and reconciliation (R13, R20-C)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st
from trading_platform.domain import (
    BrokerSnapshot,
    Instrument,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Signal,
    TimeInForce,
)
from trading_platform.oms.oms import OMS
from trading_platform.reconciliation import reconcile_positions
from trading_platform.risk.limits import PortfolioRiskLimits
from trading_platform.risk.risk_engine import HardRiskEngine, ReconciliationEngine, RiskPolicyVersion

UTC = timezone.utc

SYMBOLS = st.sampled_from(["AAPL", "MSFT", "GOOG", "AMZN", "TSLA"])
PRICES = st.floats(min_value=0.01, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)
QUANTITIES = st.integers(min_value=1, max_value=10_000)
CASH_VALUES = st.floats(min_value=0.0, max_value=10_000_000.0, allow_nan=False, allow_infinity=False)


def make_order(order_id: str, symbol: str, side: OrderSide, quantity: int, price: float | None) -> Order:
    instrument = Instrument(symbol)
    signal = Signal(instrument, side, quantity, price, OrderType.LIMIT, TimeInForce.DAY)
    return Order(
        order_id, instrument, side, quantity, price, OrderType.LIMIT, TimeInForce.DAY, OrderStatus.SUBMITTED, signal
    )


def make_engine(
    max_positions: int = 3,
    max_gross_exposure: float = 1e12,
    min_cash_reserve_pct: float = 0.0,
    commission_per_order: float = 0.0,
) -> HardRiskEngine:
    return HardRiskEngine(
        [
            RiskPolicyVersion(
                1,
                max_positions=max_positions,
                max_gross_exposure=max_gross_exposure,
                min_cash_reserve_pct=min_cash_reserve_pct,
                commission_per_order=commission_per_order,
            )
        ]
    )


@given(symbol=SYMBOLS, quantity=QUANTITIES, price=PRICES, cash=CASH_VALUES)
def test_check_order_never_breaches_cash_limit(symbol: str, quantity: int, price: float, cash: float) -> None:
    engine = make_engine(commission_per_order=1.0)
    order = make_order("prop-cash", symbol, OrderSide.BUY, quantity, price)
    approved, _reason, _policy = engine.check_order(order, {}, cash)
    cost = quantity * price + 1.0
    if approved:
        assert cost <= cash + 1e-6
    if cost > cash + 1e-6:
        assert not approved


@given(
    market_values=st.lists(PRICES, min_size=0, max_size=6),
    gross_limit=st.floats(min_value=0.0, max_value=10_000_000.0, allow_nan=False, allow_infinity=False),
)
def test_check_order_never_breaches_gross_exposure(market_values: list[float], gross_limit: float) -> None:
    positions = {
        f"SYM{index}": Position(
            Instrument(f"SYM{index}"), 1, value, market_value=value, unrealized_pnl=0.0, realized_pnl=0.0
        )
        for index, value in enumerate(market_values)
    }
    engine = make_engine(max_positions=10, max_gross_exposure=gross_limit)
    order = make_order("prop-exposure", "NEWSYM", OrderSide.BUY, 1, 100.0)
    approved, _reason, _policy = engine.check_order(order, positions, 10_000_000.0)
    gross = sum(abs(position.market_value) for position in positions.values())
    if approved:
        assert gross <= gross_limit + 1e-6
    if gross > gross_limit + 1e-6:
        assert not approved


@given(existing_count=st.integers(min_value=0, max_value=6))
def test_check_order_never_exceeds_max_three_positions(existing_count: int) -> None:
    positions = {
        f"SYM{index}": Position(Instrument(f"SYM{index}"), 1, 100.0, 100.0, 0.0, 0.0) for index in range(existing_count)
    }
    engine = make_engine(max_positions=3)
    order = make_order("prop-positions", "NEWSYM", OrderSide.BUY, 1, 100.0)
    approved, _reason, _policy = engine.check_order(order, positions, 1_000_000.0)
    if approved:
        assert existing_count < 3


@given(starting=CASH_VALUES, current=CASH_VALUES)
def test_drawdown_limit_never_exceeded(starting: float, current: float) -> None:
    limits = PortfolioRiskLimits(max_drawdown_pct=10.0)
    approved, _reason = limits.check_drawdown(0.0, starting, current)
    if starting > 0 and approved:
        assert (starting - current) / starting <= 0.10 + 1e-9


@given(starting=CASH_VALUES, current=CASH_VALUES)
def test_hard_risk_order_gate_rejects_drawdown_breaches(starting: float, current: float) -> None:
    engine = make_engine()
    order = make_order("prop-drawdown", "AAPL", OrderSide.BUY, 1, 1.0)
    approved, _reason, _policy = engine.check_order(order, {}, current, starting_cash=starting)
    if starting > 0 and (starting - current) / starting > 0.10 + 1e-9:
        assert not approved


@given(daily_loss=st.floats(min_value=0.0, max_value=10_000.0, allow_nan=False, allow_infinity=False))
def test_hard_risk_order_gate_rejects_daily_loss_breaches(daily_loss: float) -> None:
    engine = HardRiskEngine([RiskPolicyVersion(1, min_cash_reserve_pct=0, max_daily_loss=100.0)])
    order = make_order("prop-daily-loss", "AAPL", OrderSide.BUY, 1, 1.0)
    approved, _reason, _policy = engine.check_order(order, {}, 1000.0, daily_loss=daily_loss)
    if daily_loss > 100.0:
        assert not approved


@given(
    cash=CASH_VALUES,
    market_values=st.lists(PRICES, min_size=0, max_size=5),
)
def test_check_order_never_breaches_cash_reserve(cash: float, market_values: list[float]) -> None:
    positions = {
        f"SYM{index}": Position(
            Instrument(f"SYM{index}"), 1, value, market_value=value, unrealized_pnl=0.0, realized_pnl=0.0
        )
        for index, value in enumerate(market_values)
    }
    engine = make_engine(max_positions=10, min_cash_reserve_pct=25.0)
    order = make_order("prop-reserve", "NEWSYM", OrderSide.BUY, 1, 100.0)
    approved, _reason, _policy = engine.check_order(order, positions, cash, PortfolioRiskLimits())
    equity = cash + sum(abs(position.market_value) for position in positions.values())
    if approved and equity > 0:
        assert cash / equity >= 0.25 - 1e-9


@given(
    local=st.dictionaries(SYMBOLS, st.integers(min_value=-100, max_value=100)),
    broker=st.dictionaries(SYMBOLS, st.integers(min_value=-100, max_value=100)),
)
def test_reconcile_positions_report_matches_actual_differences(local: dict[str, int], broker: dict[str, int]) -> None:
    report = reconcile_positions(local, broker)
    differing = any(local.get(symbol, 0) != broker.get(symbol, 0) for symbol in set(local) | set(broker))
    assert report.matched is not differing
    assert report.blocks_new_orders is differing


def test_portfolio_risk_limits_edge_paths() -> None:
    from trading_platform.risk.limits import check_position_limit

    existing = {"AAPL": Position(Instrument("AAPL"), 1, 100.0, 100.0, 0.0, 0.0)}
    maxed, reason = check_position_limit(
        1, {"A": existing["AAPL"], "B": existing["AAPL"], "C": existing["AAPL"]}, Instrument("NEW"), 3
    )
    assert not maxed
    assert "Max positions" in reason
    reduction, reduction_reason = check_position_limit(0, existing, Instrument("AAPL"))
    assert reduction and "reduction" in reduction_reason.lower()
    increase, increase_reason = check_position_limit(1, existing, Instrument("AAPL"))
    assert increase and "Increasing" in increase_reason
    within, within_reason = check_position_limit(1, existing, Instrument("NEW"))
    assert within and "Within" in within_reason

    limits = PortfolioRiskLimits(max_turnover_pct=10.0, max_gross_exposure_pct=50.0, min_cash_reserve_pct=5.0)
    no_prior, _message = limits.check_turnover(2, 0, 0)
    assert no_prior
    exceeded, _message = limits.check_turnover(2, 0, 10)
    assert not exceeded
    turnover_ok, _message = limits.check_turnover(1, 0, 10)
    assert turnover_ok

    no_equity, message = limits.check_gross_exposure_pct(100.0, 0.0)
    assert no_equity and "No equity base" in message
    over, _message = limits.check_gross_exposure_pct(600.0, 1000.0)
    assert not over
    under, _message = limits.check_gross_exposure_pct(400.0, 1000.0)
    assert under

    no_equity_reserve, message = limits.check_cash_reserve(10.0, 0.0)
    assert no_equity_reserve and "No equity base" in message
    reserve_low, _message = limits.check_cash_reserve(10.0, 1000.0)
    assert not reserve_low
    reserve_ok, _message = limits.check_cash_reserve(100.0, 1000.0)
    assert reserve_ok


def test_sector_concentration_branches() -> None:
    from trading_platform.risk.limits import check_sector_concentration

    positions = {
        "AAPL": Position(Instrument("AAPL"), 1, 100.0, 100.0, 0.0, 0.0),
        "MSFT": Position(Instrument("MSFT"), 1, 100.0, 100.0, 0.0, 0.0),
    }
    concentrated, message = check_sector_concentration(
        Instrument("GOOG"), positions, {"AAPL": "tech", "MSFT": "tech", "GOOG": "tech"}
    )
    assert not concentrated
    assert "tech" in message
    mapped, message = check_sector_concentration(
        Instrument("GOOG"), {"AAPL": positions["AAPL"]}, {"AAPL": "tech", "GOOG": "tech"}
    )
    assert mapped
    assert "OK" in message
    unmapped, _message = check_sector_concentration(Instrument("NEW"), {}, None)
    assert unmapped


@given(
    fills=st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=50),
            st.floats(min_value=10.0, max_value=1_000.0, allow_nan=False, allow_infinity=False),
        ),
        min_size=1,
        max_size=8,
    ),
    skew=st.integers(min_value=1, max_value=10),
)
def test_reconciliation_never_passes_with_a_real_mismatch(fills: list[tuple[int, float]], skew: int) -> None:
    oms = OMS("property")
    recon = ReconciliationEngine(oms)
    beginning_cash = 1_000_000.0
    expected_positions: dict[str, float] = {}
    expected_cash = beginning_cash
    for index, (quantity, price) in enumerate(fills):
        order_id = f"prop-{index}"
        order = make_order(order_id, "AAPL", OrderSide.BUY, quantity, price)
        accepted, _reason = oms.submit_order(order, f"idem-{index}")
        assert accepted
        assert oms.accept_order(order_id)
        assert oms.open_order(order_id)
        assert oms.fill_order(order_id, quantity, price, execution_id=f"exec-{index}", commission=1.0, slippage=0.05)
        expected_positions["AAPL"] = expected_positions.get("AAPL", 0.0) + quantity
        expected_cash -= quantity * price + 1.0 + 0.05

    internal = recon.build_internal_snapshot()
    assert internal["fill_count"] == len(fills)
    assert internal["positions"] == expected_positions

    snapshot = BrokerSnapshot(
        timestamp=datetime.now(UTC),
        positions={Instrument("AAPL"): float(expected_positions["AAPL"])},
        cash=expected_cash,
        buying_power=expected_cash,
    )
    result = recon.reconcile_against_snapshot(snapshot, beginning_cash=beginning_cash)
    assert result["overall_status"] == "PASS"
    assert not result["blocks_new_orders"]

    mismatched = BrokerSnapshot(
        timestamp=datetime.now(UTC),
        positions={Instrument("AAPL"): float(expected_positions["AAPL"]) + skew},
        cash=expected_cash,
        buying_power=expected_cash,
    )
    failed = recon.reconcile_against_snapshot(mismatched, beginning_cash=beginning_cash)
    assert failed["overall_status"] == "MISMATCH"
    assert failed["blocks_new_orders"]
    assert failed["operator_resolution_required"]
    assert "AAPL" in failed["differences"][0]


@pytest.mark.parametrize("quantity,price,cash", [(1, 100.0, 100.0), (3, 33.34, 100.0), (10, 0.01, 0.11)])
def test_buying_power_rejects_when_cost_exceeds_cash(quantity: int, price: float, cash: float) -> None:
    from trading_platform.risk.limits import check_buying_power

    approved, _reason = check_buying_power(quantity, price, cash, {}, 1.0)
    assert not approved or quantity * price + 1.0 <= cash + 1e-6
