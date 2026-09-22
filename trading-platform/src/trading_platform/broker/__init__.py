"""Broker adapter package: the BrokerAdapter contract, the deterministic fake
adapter, and the IBKR paper client adapter.

The IBKR paper client imports ib_async lazily inside
trading_platform.broker.ibkr_paper; no module in this package imports it at
module level, so default offline tests need no IBKR library.
"""

from trading_platform.broker.base import BrokerAdapter
from trading_platform.broker.fake import FakeBrokerAdapter
from trading_platform.broker.ibkr_paper import IBKRPaperBrokerAdapter

__all__ = ["BrokerAdapter", "FakeBrokerAdapter", "IBKRPaperBrokerAdapter"]
