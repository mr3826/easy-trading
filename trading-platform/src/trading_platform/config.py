"""Configuration gates with secure defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeConfig:
    environment: str
    live_trading_enabled: bool
    live_status: str


def load_config() -> RuntimeConfig:
    """Load configuration; live execution cannot be enabled by one flag."""
    enabled = os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"
    status = os.getenv("LIVE_STATUS", "NOT_AUTHORIZED")
    if enabled or status != "NOT_AUTHORIZED":
        enabled = False
        status = "NOT_AUTHORIZED"
    return RuntimeConfig(os.getenv("TRADING_ENV", "research"), enabled, status)
