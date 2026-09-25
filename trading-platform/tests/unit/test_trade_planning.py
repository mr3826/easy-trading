"""TradePlan / SignalEvidence / position-sizing tests."""

from __future__ import annotations

import pytest
from trading_platform.trade_planning import (
    PortfolioState,
    SignalEvidence,
    SizingPolicy,
    TradePlan,
    TradePlanningError,
    build_trade_plan,
    rank_evidence,
    size_position,
)


def _evidence(**kwargs) -> SignalEvidence:
    base = dict(
        symbol="AAPL",
        decision_timestamp="2026-01-05T14:30:00+00:00",
        strategy_id="trend_rs_m63_rs63_atr2.5",
        strategy_version="1.0.0",
        market_regime={"trend": "bullish", "volatility": "normal", "liquidity": "acceptable"},
        raw_signal="BUY",
        trend_confirmation=True,
        relative_strength=0.05,
        volatility_state="normal",
        liquidity_state="acceptable",
        volume_confirmation=True,
        feature_snapshot_hash="ab" * 32,
        expected_entry=100.0,
        initial_stop=92.0,
        planned_exit="atr_trailing",
        estimated_transaction_cost=3.0,
        signal_score=0.05,
        rejection_reasons=(),
    )
    base.update(kwargs)
    return SignalEvidence(**base)


def _portfolio(**kwargs) -> PortfolioState:
    base = dict(equity=100_000.0, settled_cash=100_000.0, gross_exposure=0.0, open_positions=0)
    base.update(kwargs)
    return PortfolioState(**base)


def test_evidence_eligibility_requires_clean_signal() -> None:
    assert _evidence().eligible
    assert not _evidence(raw_signal="HOLD").eligible
    assert not _evidence(rejection_reasons=("liquidity regime unacceptable",)).eligible


def test_score_cannot_redeem_ineligible() -> None:
    bad = _evidence(raw_signal="BUY", rejection_reasons=("no breakout",), signal_score=99.0)
    good = _evidence(symbol="MSFT", signal_score=1.0)
    ranked = rank_evidence([bad, good])
    assert ranked[0] is good  # higher-scored but ineligible candidate ranks last
    assert ranked[-1] is bad


def test_risk_based_sizing_math() -> None:
    # risk_budget = 1000, risk_per_share = 8 -> floor(125) shares
    result = size_position(_evidence(), _portfolio(), SizingPolicy())
    assert result.accepted
    assert result.quantity == 125
    assert result.risk_per_share == pytest.approx(8.0)
    assert result.binding_constraint == "risk_budget"


def test_cash_constraint_binds() -> None:
    result = size_position(_evidence(), _portfolio(settled_cash=10_000.0), SizingPolicy())
    # cash after reserve: 10000 - 5000 = 5000 -> floor(5000/100) = 50
    assert result.quantity == 50
    assert result.binding_constraint == "settled_cash"


def test_max_positions_rejects() -> None:
    result = size_position(_evidence(), _portfolio(open_positions=3), SizingPolicy())
    assert not result.accepted
    assert result.binding_constraint == "max_positions"


def test_gross_exposure_cap_binds() -> None:
    result = size_position(_evidence(), _portfolio(gross_exposure=85_000.0), SizingPolicy(max_gross_exposure_pct=0.9))
    # exposure room = 90000 - 85000 = 5000 -> 50 shares
    assert result.quantity == 50
    assert result.binding_constraint == "gross_exposure"


def test_position_notional_cap_binds() -> None:
    result = size_position(_evidence(), _portfolio(), SizingPolicy(max_position_notional_pct=0.05))
    assert result.quantity == 50
    assert result.binding_constraint == "position_notional"


def test_sector_cap_binds() -> None:
    result = size_position(
        _evidence(),
        _portfolio(symbol_sector="tech", sector_exposure=18_000.0),
        SizingPolicy(),
        sector_cap_pct=0.2,
    )
    # sector room = 20000 - 18000 = 2000 -> 20 shares
    assert result.quantity == 20
    assert result.binding_constraint == "sector_cap"


def test_ineligible_evidence_rejected() -> None:
    result = size_position(_evidence(rejection_reasons=("x",)), _portfolio(), SizingPolicy())
    assert not result.accepted


def test_invalid_risk_per_share_rejected() -> None:
    result = size_position(_evidence(initial_stop=100.0), _portfolio(), SizingPolicy())
    assert not result.accepted


def test_policy_validation() -> None:
    with pytest.raises(TradePlanningError):
        SizingPolicy(risk_fraction=0.0)
    with pytest.raises(TradePlanningError):
        SizingPolicy(max_position_notional_pct=1.5)


def test_trade_plan_construction() -> None:
    plan, rejections = build_trade_plan(_evidence(), _portfolio(), SizingPolicy())
    assert plan is not None and not rejections
    assert plan.direction == "LONG"
    assert plan.quantity == 125
    assert plan.expected_risk_per_share == pytest.approx(8.0)
    assert len(plan.plan_id()) == 64
    assert plan.expected_total_cost == pytest.approx(plan.estimated_fees + plan.estimated_slippage)


def test_trade_plan_rejects_short_and_bad_stops() -> None:
    ev = _evidence()
    with pytest.raises(TradePlanningError):
        TradePlan(
            symbol="AAPL",
            direction="SHORT",
            strategy_id=ev.strategy_id,
            strategy_version=ev.strategy_version,
            decision_timestamp=ev.decision_timestamp,
            entry_style="MARKET_NEXT_OPEN",
            estimated_entry=100.0,
            quantity=1,
            risk_budget=1000.0,
            initial_stop=92.0,
            take_profit=None,
            trailing_stop=None,
            max_holding_days=30,
            expected_risk_per_share=8.0,
            estimated_fees=2.0,
            estimated_slippage=0.2,
            expected_total_cost=2.2,
            evidence=ev,
        )
    with pytest.raises(TradePlanningError):
        TradePlan(
            symbol="AAPL",
            direction="LONG",
            strategy_id=ev.strategy_id,
            strategy_version=ev.strategy_version,
            decision_timestamp=ev.decision_timestamp,
            entry_style="MARKET_NEXT_OPEN",
            estimated_entry=100.0,
            quantity=1,
            risk_budget=1000.0,
            initial_stop=101.0,
            take_profit=None,
            trailing_stop=None,
            max_holding_days=30,
            expected_risk_per_share=8.0,
            estimated_fees=2.0,
            estimated_slippage=0.2,
            expected_total_cost=2.2,
            evidence=ev,
        )
    with pytest.raises(TradePlanningError):
        TradePlan(
            symbol="AAPL",
            direction="LONG",
            strategy_id=ev.strategy_id,
            strategy_version=ev.strategy_version,
            decision_timestamp=ev.decision_timestamp,
            entry_style="MARKET_NEXT_OPEN",
            estimated_entry=100.0,
            quantity=1,
            risk_budget=1000.0,
            initial_stop=92.0,
            take_profit=None,
            trailing_stop=None,
            max_holding_days=30,
            expected_risk_per_share=8.0,
            estimated_fees=2.0,
            estimated_slippage=0.2,
            expected_total_cost=2.2,
            evidence=_evidence(rejection_reasons=("bad",)),
        )


def test_plan_rejection_propagates_sizing_reasons() -> None:
    plan, rejections = build_trade_plan(_evidence(), _portfolio(open_positions=3), SizingPolicy())
    assert plan is None
    assert rejections
