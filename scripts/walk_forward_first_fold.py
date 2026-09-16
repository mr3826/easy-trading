"""Small, safe walk-forward smoke example.

This script is intentionally not production evidence and performs no broker
operations. It exists to exercise the public simulator boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone

from trading_platform.domain import Bar, Instrument
from trading_platform.simulator.event_driven_simulator import EventDrivenSimulator


def main() -> int:
    instrument = Instrument("AAPL")
    bars = [
        Bar(instrument, datetime(2026, 1, 2, tzinfo=timezone.utc), 100, 101, 99, 100, 1000),
        Bar(instrument, datetime(2026, 1, 3, tzinfo=timezone.utc), 101, 102, 100, 101, 1000),
    ]
    EventDrivenSimulator().run(bars, [])
    print("walk-forward smoke completed; no strategy or broker evidence generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
