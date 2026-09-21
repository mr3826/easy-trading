"""Hypothesis property-based tests for domain value objects and market data.

These tests exercise invariants with varied inputs: prices, quantities,
events, and timestamps.
"""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from typing import Any, Dict

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from trading_platform.data import validate_bar
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
