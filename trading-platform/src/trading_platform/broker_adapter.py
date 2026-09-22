"""Backward-compatible re-exports for the broker package.

The adapter implementations moved to :mod:`trading_platform.broker`
(ADR-008). This module keeps the historical import path working; new code
should import from ``trading_platform.broker`` directly.
"""

from trading_platform.broker.base import BrokerAdapter
from trading_platform.broker.fake import FakeBrokerAdapter
from trading_platform.broker.ibkr_paper import IBKRPaperBrokerAdapter

__all__ = ["BrokerAdapter", "FakeBrokerAdapter", "IBKRPaperBrokerAdapter"]
