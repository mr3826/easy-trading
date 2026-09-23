from datetime import datetime, timezone

import pytest
from trading_platform.authorization import AuthorizationRequest, authorize_live
from trading_platform.config import load_config
from trading_platform.domain import (
    Bar,
    Instrument,
    OrderSide,
    OrderType,
    Signal,
    TimeInForce,
)
from trading_platform.features import FeatureRejected, parse_news_feature
from trading_platform.reconciliation import reconcile_positions
from trading_platform.shadow import ShadowBrokerAdapter, run_shadow


def test_live_authorization_is_permanently_disabled() -> None:
    request = AuthorizationRequest(
        "live",
        "LIVE-TEST",
        "policy-v1",
        True,
        False,
        True,
        True,
        1000,
        1,
        10,
        datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    assert not authorize_live(request, now=datetime(2027, 1, 1, tzinfo=timezone.utc)).approved


def test_reconciliation_mismatch_blocks_new_orders() -> None:
    report = reconcile_positions({"AAPL": 10}, {"AAPL": 9})
    assert report.blocks_new_orders
    assert not report.matched


def test_shadow_records_without_submission_path() -> None:
    instrument = Instrument("AAPL")
    bar = Bar(instrument, datetime(2026, 1, 1, tzinfo=timezone.utc), 1, 2, 1, 2, 100)
    signal = Signal(instrument, OrderSide.BUY, 1, None, OrderType.MARKET, TimeInForce.DAY)
    sink = ShadowBrokerAdapter()
    result = run_shadow([bar], lambda _: signal, sink)
    assert result[0].action == "WOULD_SUBMIT"
    assert not hasattr(sink, "execute_order")


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        '{"symbol":"AAPL"}',
        '{"symbol":"MSFT","sentiment":0,"confidence":2,"observed_at":"x"}',
    ],
)
def test_untrusted_feature_fails_closed(raw: str) -> None:
    with pytest.raises(FeatureRejected):
        parse_news_feature(raw, {"AAPL"})


@pytest.mark.parametrize(
    "raw",
    [
        '{"symbol":"AAPL","sentiment":NaN,"confidence":0.5,"observed_at":"x"}',
        '{"symbol":"AAPL","sentiment":0,"confidence":Infinity,"observed_at":"x"}',
        '{"symbol":"AAPL","sentiment":0,"confidence":0.5,"observed_at":"ignore previous"}',
    ],
)
def test_feature_nan_and_injection_fail_closed(raw: str) -> None:
    with pytest.raises(FeatureRejected) as error:
        parse_news_feature(raw, {"AAPL"})
    assert str(error.value).startswith("FEATURE_REJECTED")


def test_default_config_cannot_authorize_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    monkeypatch.setenv("LIVE_STATUS", "AUTHORIZED")
    config = load_config()
    assert not config.live_trading_enabled
    assert config.live_status == "NOT_AUTHORIZED"
