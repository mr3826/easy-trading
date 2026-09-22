"""External IBKR paper smoke tests.

Skipped by default: IBKR_PAPER_SMOKE=1 plus IBKR_PAPER_HOST/PORT/CLIENT_ID are
required. The connect-only test performs no order submission; the submission
test additionally requires IBKR_PAPER_ALLOW_SUBMISSION=1. Live ports and live
account configuration are rejected here as well, before any connection.
"""

from __future__ import annotations

import os

import pytest
from trading_platform.broker.ibkr_paper import IBKRPaperBrokerAdapter, validate_paper_target
from trading_platform.domain import (
    Instrument,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Signal,
    TimeInForce,
)

pytestmark = pytest.mark.external


def _smoke_config() -> dict[str, str | int] | None:
    if os.environ.get("IBKR_PAPER_SMOKE") != "1":
        return None
    host = os.environ.get("IBKR_PAPER_HOST")
    port = os.environ.get("IBKR_PAPER_PORT")
    client_id = os.environ.get("IBKR_PAPER_CLIENT_ID")
    if not host or not port or not client_id:
        return None
    return {
        "host": host,
        "port": int(port),
        "client_id": int(client_id),
        "account": os.environ.get("IBKR_PAPER_ACCOUNT", ""),
    }


@pytest.fixture
def smoke_config() -> dict[str, str | int]:
    config = _smoke_config()
    if config is None:
        pytest.skip("IBKR_PAPER_SMOKE=1 and IBKR_PAPER_HOST/PORT/CLIENT_ID are required")
    validate_paper_target(str(config["host"]), int(config["port"]), str(config["account"]))
    return config


@pytest.fixture
def connected_smoke_adapter(smoke_config):
    adapter = IBKRPaperBrokerAdapter(allow_connection=True, **smoke_config)
    assert adapter.start(), adapter.get_last_error()
    yield adapter
    adapter.stop()


def test_connect_only_smoke(connected_smoke_adapter) -> None:
    adapter = connected_smoke_adapter
    assert adapter.connected
    assert adapter.broker_id == "IBKR"
    snapshot = adapter.get_account_snapshot()
    assert snapshot.status == "connected"


def test_paper_submission_smoke(smoke_config) -> None:
    if os.environ.get("IBKR_PAPER_ALLOW_SUBMISSION") != "1":
        pytest.skip("IBKR_PAPER_ALLOW_SUBMISSION=1 is required for the submission smoke test")
    adapter = IBKRPaperBrokerAdapter(allow_connection=True, allow_paper_orders=True, **smoke_config)
    assert adapter.start(), adapter.get_last_error()
    try:
        instrument = Instrument("SPY")
        signal = Signal(instrument, OrderSide.BUY, 1, None, OrderType.MARKET, TimeInForce.DAY)
        order = Order(
            "ibkr-smoke-market-1",
            instrument,
            OrderSide.BUY,
            1,
            None,
            OrderType.MARKET,
            TimeInForce.DAY,
            OrderStatus.SUBMITTED,
            signal,
        )
        result = adapter.execute_order(order, None)
        assert result["order_id"] == "ibkr-smoke-market-1"
        assert result["status"] is not None
    finally:
        adapter.stop()
