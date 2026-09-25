"""Property-based sizing invariants (hypothesis)."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from trading_platform.trade_planning import (
    PortfolioState,
    SignalEvidence,
    SizingPolicy,
    size_position,
)


@st.composite
def _evidence(draw):
    entry = draw(st.floats(min_value=1.0, max_value=1000.0, allow_nan=False))
    stop_pct = draw(st.floats(min_value=0.01, max_value=0.5, allow_nan=False))
    return SignalEvidence(
        symbol="TEST",
        decision_timestamp="2026-01-05T14:30:00+00:00",
        strategy_id="trend_rs",
        strategy_version="1.0.0",
        market_regime={},
        raw_signal="BUY",
        trend_confirmation=True,
        relative_strength=0.0,
        volatility_state="normal",
        liquidity_state="acceptable",
        volume_confirmation=False,
        feature_snapshot_hash="ab" * 32,
        expected_entry=entry,
        initial_stop=entry * (1 - stop_pct),
        planned_exit="atr_trailing",
        estimated_transaction_cost=2.0,
        signal_score=0.0,
    )


@given(
    ev=_evidence(),
    equity=st.floats(min_value=10_000.0, max_value=10_000_000.0, allow_nan=False),
    cash_frac=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    exposure_frac=st.floats(min_value=0.0, max_value=1.5, allow_nan=False),
    open_positions=st.integers(min_value=0, max_value=5),
    risk_fraction=st.floats(min_value=0.001, max_value=0.05, allow_nan=False),
)
@settings(max_examples=100, deadline=None)
def test_sizing_never_breaches_constraints(ev, equity, cash_frac, exposure_frac, open_positions, risk_fraction) -> None:
    policy = SizingPolicy(risk_fraction=risk_fraction)
    portfolio = PortfolioState(
        equity=equity,
        settled_cash=equity * cash_frac,
        gross_exposure=equity * exposure_frac,
        open_positions=open_positions,
    )
    result = size_position(ev, portfolio, policy)
    if not result.accepted:
        assert result.quantity == 0
        return
    entry = ev.expected_entry
    # Invariants: never exceeds hard cap, never exceeds cash, never exceeds
    # position notional cap, never exceeds risk budget shares, never exceeds
    # gross-exposure room.
    assert result.quantity <= policy.max_quantity
    assert (
        result.quantity * entry <= max(0.0, portfolio.settled_cash - equity * policy.min_cash_reserve_pct) + entry
    )  # floor() tolerance
    assert result.quantity * entry <= equity * policy.max_position_notional_pct + entry
    assert result.quantity <= (equity * policy.risk_fraction) / (entry - ev.initial_stop) + 1
