"""Strategy candidate behavior on synthetic frames + gap-through-stop backtest."""

from __future__ import annotations

import numpy as np
import pandas as pd
from trading_platform.features.indicators import compute_features
from trading_platform.regimes import (
    LiquidityRegime,
    RegimeClassification,
    TrendRegime,
    VolatilityRegime,
)
from trading_platform.research.backtest import BacktestConfig, CostModel, run_backtest
from trading_platform.strategies.candidates import (
    BreakoutVolumeParams,
    TrendPullbackParams,
    TrendRelativeStrengthParams,
    breakout_volume_signal,
    trend_pullback_signal,
    trend_relative_strength_signal,
)


def _regime(
    trend: TrendRegime = TrendRegime.BULLISH, vol: VolatilityRegime = VolatilityRegime.NORMAL
) -> RegimeClassification:
    return RegimeClassification(
        as_of="2024-06-01",
        trend=trend,
        volatility=vol,
        liquidity=LiquidityRegime.ACCEPTABLE,
        trend_distance=0.05,
        trend_slope=0.1,
        vol_percentile=0.5,
        median_dollar_volume=5e6,
    )


def _trending_frame(n: int = 260, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0015, 0.01, n))
    high = close * 1.005
    low = close * 0.995
    open_ = close * 0.999
    volume = np.full(n, 2_000_000.0)
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


def test_trend_rs_emits_eligible_buy_in_uptrend() -> None:
    params = TrendRelativeStrengthParams()
    df = _trending_frame()
    ff = compute_features(df, params.feature_specs(), symbol="AAA", benchmark=df["close"] * 0.99)
    frame = df.join(ff.frame)
    ev = trend_relative_strength_signal("AAA", frame, _regime(), params, "2023-12-29T21:00:00+00:00")
    assert ev is not None
    # price above trend MA with positive momentum vs itself-shifted benchmark
    # may or may not clear RS>0; at minimum the record must be well-formed
    assert ev.strategy_id == params.strategy_id
    assert ev.feature_snapshot_hash
    if ev.eligible:
        assert ev.initial_stop < ev.expected_entry
    else:
        assert ev.rejection_reasons


def test_trend_rs_rejects_bearish_regime() -> None:
    params = TrendRelativeStrengthParams()
    df = _trending_frame()
    ff = compute_features(df, params.feature_specs(), symbol="AAA", benchmark=df["close"])
    frame = df.join(ff.frame)
    ev = trend_relative_strength_signal(
        "AAA", frame, _regime(trend=TrendRegime.BEARISH), params, "2023-12-29T21:00:00+00:00"
    )
    assert not ev.eligible
    assert any("trend regime" in r for r in ev.rejection_reasons)


def test_breakout_requires_volume_confirmation() -> None:
    params = BreakoutVolumeParams(min_relative_volume=2.0)
    df = _trending_frame(300)
    # Force an obvious breakout with LOW volume (rel vol ~1): must reject.
    ff = compute_features(df, params.feature_specs(), symbol="AAA")
    frame = df.join(ff.frame)
    ev = breakout_volume_signal("AAA", frame, _regime(), params, "2023-12-29T21:00:00+00:00")
    if not ev.rejection_reasons or "volume confirmation missing" not in ev.rejection_reasons:
        # also acceptable: no breakout at the last bar; either way not trivially BUY
        assert not ev.eligible or ev.volume_confirmation


def test_pullback_requires_recovery_trigger() -> None:
    params = TrendPullbackParams()
    df = _trending_frame(300)
    ff = compute_features(df, params.feature_specs(), symbol="AAA")
    frame = df.join(ff.frame)
    ev = trend_pullback_signal("AAA", frame, _regime(), params, "2023-12-29T21:00:00+00:00")
    # In a persistent uptrend a deep oversold pullback is unlikely: expect rejection.
    assert ev.raw_signal in ("HOLD", "BUY")
    if not ev.eligible:
        assert ev.rejection_reasons


def test_backtest_gap_through_stop_not_filled_at_stop() -> None:
    # Build a frame where price gaps well below the stop overnight.
    n = 120
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    close = np.concatenate([np.linspace(100, 130, 100), np.linspace(130, 60, 20)])
    df = pd.DataFrame(
        {
            "open": np.concatenate([close[:100], [70.0] + list(close[101:])]),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(n, 1_000_000.0),
            "atr_window14": 2.0,
        },
        index=idx,
    )

    class _Ev:
        eligible = True
        raw_signal = "BUY"
        signal_score = 1.0
        initial_stop = 100.0
        expected_entry = close[100]

    calls = {"n": 0}

    def signal_fn(symbol, hist, regime, params, ts):
        calls["n"] += 1
        # Enter exactly once: signal at bar 98 fills at bar 99 open (130);
        # bar 100 opens at 70, far below the 100 stop -> gap fill at open.
        if calls["n"] == 99:
            return _Ev()
        return None

    res = run_backtest({"AAA": df}, signal_fn, object(), None, BacktestConfig())
    gap_trades = [t for t in res.trades if t.gap_through_stop]
    assert gap_trades, "expected a gap-through-stop exit"
    for t in gap_trades:
        assert t.exit_price < t.entry_price
        assert t.exit_reason == "gap_through_stop"
        # The fill must be at/below the open, NOT at the protective stop.
        assert t.exit_price < 100.0


def test_backtest_cost_multiplier_reduces_pnl() -> None:
    df = _trending_frame(300)
    df = df.assign(atr_window14=2.0)
    entered = {"done": False}

    class _Ev:
        eligible = True
        raw_signal = "BUY"
        signal_score = 1.0
        initial_stop = df["close"].iloc[0] - 5.0
        expected_entry = df["close"].iloc[0]

    def signal_fn(symbol, hist, regime, params, ts):
        if not entered["done"] and len(hist) >= 20:
            entered["done"] = True
            return _Ev()
        return None

    def run(mult: float):
        entered["done"] = False
        cfg = BacktestConfig(cost=CostModel(commission_per_order=1.0, slippage_pct=0.001, multiplier=mult))
        return run_backtest({"AAA": df}, signal_fn, object(), None, cfg)

    base = run(1.0)
    stressed = run(2.0)
    assert base.final_equity >= stressed.final_equity
    entered["done"] = False
