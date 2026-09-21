"""Hypothesis property-based tests for domain value objects and market data.

These tests exercise invariants with varied inputs: prices, quantities,
events, and timestamps.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from trading_platform.data import FakeMarketDataProvider, ParquetMarketDataProvider, validate_bar
from trading_platform.domain import (
    Bar,
    Instrument,
    JournalEvent,
    OrderSide,
    OrderType,
    Signal,
    TimeInForce,
    redact_secrets,
)

UTC = timezone.utc

positive_price = st.floats(min_value=1.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)
non_positive = st.integers(max_value=0)
negative = st.integers(max_value=-1)
naive_timestamp = st.datetimes()
symbol_chars = st.characters(min_codepoint=33, max_codepoint=126, exclude_categories=("Cs",))
symbol_strategy = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.", min_size=1, max_size=16)


def journal_event_kwargs(
    event_id: str, payload: Dict[str, Any], checksum: str, timestamp: datetime | None = None
) -> Dict[str, Any]:
    return {
        "event_id": event_id,
        "timestamp": timestamp or datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC),
        "environment": "simulation",
        "code_version": "v1.0.0",
        "config_version": "v1.0.0",
        "event_type": "order_submitted",
        "payload": payload,
        "source": "strategy",
        "checksum": checksum,
    }


@settings(deadline=None)
@given(low=positive_price, high=positive_price, open_=positive_price, close=positive_price)
def test_property_any_positive_ohlc_constructs_valid_bar(low: float, high: float, open_: float, close: float) -> None:
    low, high = min(low, high), max(low, high)
    open_ = min(max(open_, low), high)
    close = min(max(close, low), high)
    bar = Bar(
        instrument=Instrument("prop"),
        timestamp=datetime(2026, 1, 15, tzinfo=UTC),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100,
    )
    assert bar.high >= max(bar.open, bar.close)
    assert bar.low <= min(bar.open, bar.close)
    assert bar.bar_date == "2026-01-15"
    assert validate_bar(bar) == (True, None)
    with pytest.raises(FrozenInstanceError):
        bar.close = 1.0


@settings(deadline=None)
@given(quantity=non_positive)
def test_property_non_positive_signal_quantity_rejected(quantity: int) -> None:
    with pytest.raises(ValueError):
        _ = Signal(
            instrument=Instrument("prop"),
            side=OrderSide.BUY,
            quantity=quantity,
            price=100.0,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
        )


@settings(deadline=None)
@given(volume=negative)
def test_property_negative_bar_volume_rejected(volume: int) -> None:
    with pytest.raises(ValueError):
        _ = Bar(
            instrument=Instrument("prop"),
            timestamp=datetime(2026, 1, 15, tzinfo=UTC),
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0,
            volume=volume,
        )


@settings(deadline=None)
@given(ts=naive_timestamp)
def test_property_naive_timestamps_rejected(ts: datetime) -> None:
    with pytest.raises(ValueError):
        _ = Bar(
            instrument=Instrument("prop"),
            timestamp=ts,
            open=100.0,
            high=102.0,
            low=99.0,
            close=101.0,
            volume=100,
        )
    with pytest.raises(ValueError):
        _ = JournalEvent(**journal_event_kwargs("prop-ts", {"instrument": "AAPL"}, "hash", timestamp=ts))


@settings(deadline=None)
@given(
    event_id=st.text(alphabet=symbol_chars, min_size=1, max_size=32),
    quantity=st.integers(min_value=1, max_value=1000),
)
def test_property_duplicate_event_ids_yield_equal_checksums(event_id: str, quantity: int) -> None:
    checksum = hashlib.sha256(f"{event_id}:{quantity}".encode()).hexdigest()
    kwargs = journal_event_kwargs(event_id, {"instrument": "AAPL", "quantity": quantity}, checksum)
    first = JournalEvent(**kwargs)
    second = JournalEvent(**kwargs)
    assert first.checksum == second.checksum
    assert first == second


@settings(deadline=None)
@given(event_id=st.text(alphabet=symbol_chars, min_size=1, max_size=32))
def test_property_journal_event_frozen_immutability(event_id: str) -> None:
    event = JournalEvent(**journal_event_kwargs(event_id, {"instrument": "AAPL"}, "hash"))
    with pytest.raises(FrozenInstanceError):
        event.event_id = "mutated"
    with pytest.raises(FrozenInstanceError):
        event.payload = {}


@settings(deadline=None)
@given(symbol=symbol_strategy)
def test_property_instrument_symbol_normalization_and_freeze(symbol: str) -> None:
    inst = Instrument(symbol=f"  {symbol.lower()}  ")
    assert inst.symbol == symbol.upper()
    assert hash(inst) == hash(Instrument(symbol=symbol))
    with pytest.raises(FrozenInstanceError):
        inst.symbol = "MSFT"


@settings(deadline=None)
@given(secret=st.text(alphabet=symbol_chars, min_size=8, max_size=64))
def test_property_secret_redaction_masks_sensitive_values(secret: str) -> None:
    payload: Dict[str, Any] = {"api_key": secret, "password": secret, "instrument": "AAPL", "quantity": 10}
    redacted = redact_secrets(payload)
    assert redacted["api_key"] == "***REDACTED***"
    assert redacted["password"] == "***REDACTED***"
    assert redacted["instrument"] == "AAPL"
    assert redacted["quantity"] == 10
    assert secret not in redacted.values()
    assert payload["api_key"] == secret


def _clear_dir(path) -> None:
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


@settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(count=st.integers(min_value=1, max_value=10), seed_open=positive_price)
def test_property_parquet_roundtrip_preserves_bars(tmp_path, count: int, seed_open: float) -> None:
    _clear_dir(tmp_path)
    instrument = Instrument("roundtrip")
    bars = [
        Bar(
            instrument,
            datetime(2026, 1, day, tzinfo=UTC),
            seed_open + day,
            seed_open + day + 2,
            seed_open + day - 1,
            seed_open + day + 1,
            100 + day,
            available_at=datetime(2026, 1, day, 12, 0, tzinfo=UTC),
        )
        for day in range(1, count + 1)
    ]
    provider = ParquetMarketDataProvider(tmp_path)
    provider.write_bars(bars, source="property-test", schema_version="v1", dataset_version="v1")
    read_back = provider.get_bars(
        instrument,
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, count, tzinfo=UTC),
        as_of=datetime(2026, 12, 31, tzinfo=UTC),
    )
    assert len(read_back) == count
    for original, loaded in zip(bars, read_back):
        assert loaded.timestamp == original.timestamp
        assert (loaded.open, loaded.high, loaded.low, loaded.close) == (
            original.open,
            original.high,
            original.low,
            original.close,
        )
        assert loaded.volume == original.volume
        assert loaded.available_at == original.available_at


@settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(day=st.integers(min_value=1, max_value=28), hours_after=st.integers(min_value=0, max_value=48))
def test_property_parquet_asof_never_leaks_future_bars(tmp_path, day: int, hours_after: int) -> None:
    _clear_dir(tmp_path)
    instrument = Instrument("leakcheck")
    bar = Bar(
        instrument,
        datetime(2026, 1, day, tzinfo=UTC),
        100.0,
        102.0,
        99.0,
        101.0,
        100,
        available_at=datetime(2026, 1, day, 12, 0, tzinfo=UTC),
    )
    provider = ParquetMarketDataProvider(tmp_path)
    provider.write_bars([bar], source="property-test", schema_version="v1", dataset_version="v1")
    before_available = datetime(2026, 1, day, 11, 59, tzinfo=UTC)
    assert provider.get_bars(instrument, bar.timestamp, bar.timestamp, as_of=before_available) == []
    at_available = datetime(2026, 1, day, 12, 0, tzinfo=UTC) + timedelta(hours=hours_after)
    assert len(provider.get_bars(instrument, bar.timestamp, bar.timestamp, as_of=at_available)) == 1
    assert len(provider.get_bars(instrument, bar.timestamp, bar.timestamp)) == 1


def test_fake_market_data_provider_deterministic_asof_and_duplicate_rejection() -> None:
    instrument = Instrument("fake")
    first = Bar(instrument, datetime(2026, 1, 1, tzinfo=UTC), 100.0, 102.0, 99.0, 101.0, 1000)
    second = Bar(
        instrument,
        datetime(2026, 1, 2, tzinfo=UTC),
        101.0,
        103.0,
        100.0,
        102.0,
        1100,
        available_at=datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
    )
    provider = FakeMarketDataProvider([first, second])
    assert [bar.close for bar in provider.get_bars(instrument, first.timestamp, second.timestamp)] == [101.0, 102.0]
    assert provider.get_bars(
        instrument, first.timestamp, second.timestamp, as_of=datetime(2026, 1, 2, 11, 0, tzinfo=UTC)
    ) == [first]
    assert provider.get_metadata(instrument) == provider.get_metadata(instrument)
    assert (
        provider.get_metadata(instrument).checksum
        == FakeMarketDataProvider([first, second]).get_metadata(instrument).checksum
    )
    with pytest.raises(ValueError):
        provider.add_bar(Bar(instrument, first.timestamp, 100.0, 102.0, 99.0, 101.0, 1000))
    with pytest.raises(FileNotFoundError):
        FakeMarketDataProvider().get_metadata(Instrument("missing"))
