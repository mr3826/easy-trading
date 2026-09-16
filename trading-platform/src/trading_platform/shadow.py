"""Shadow execution that can record intent but cannot submit orders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from trading_platform.domain import Bar, Signal


@dataclass(frozen=True)
class ShadowDecision:
    symbol: str
    action: str
    quantity: int
    bar_timestamp: str


class ShadowBrokerAdapter:
    """A sink-only adapter; submission is structurally unavailable."""

    def __init__(self) -> None:
        self.decisions: list[ShadowDecision] = []

    def record_would_submit(self, signal: Signal, bar: Bar) -> ShadowDecision:
        decision = ShadowDecision(
            signal.instrument.symbol,
            "WOULD_SUBMIT",
            signal.quantity,
            bar.timestamp.isoformat(),
        )
        self.decisions.append(decision)
        return decision


def run_shadow(
    bars: Iterable[Bar],
    strategy: Callable[[Bar], Signal | None],
    sink: ShadowBrokerAdapter,
) -> list[ShadowDecision]:
    """Run strategy against completed bars and record intents only."""
    for bar in bars:
        signal = strategy(bar)
        if signal is not None:
            sink.record_would_submit(signal, bar)
    return list(sink.decisions)
