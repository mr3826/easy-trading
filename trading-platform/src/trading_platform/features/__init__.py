"""Validated, broker-independent feature boundaries."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FeatureRejected(Exception):
    reason: str

    def __str__(self) -> str:
        return f"FEATURE_REJECTED: {self.reason}"


@dataclass(frozen=True)
class NewsFeature:
    symbol: str
    sentiment: float
    confidence: float
    observed_at: str


_INJECTION = re.compile(r"(ignore\s+(all\s+)?previous|system\s+message|tool\s+call)", re.I)


def parse_news_feature(raw: str, allowed_symbols: set[str]) -> NewsFeature:
    """Parse untrusted model output without importing execution code."""
    try:
        value: Any = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise FeatureRejected("invalid JSON") from exc
    if not isinstance(value, dict) or set(value) != {
        "symbol",
        "sentiment",
        "confidence",
        "observed_at",
    }:
        raise FeatureRejected("schema violation")
    symbol = value["symbol"]
    if not isinstance(symbol, str) or symbol not in allowed_symbols:
        raise FeatureRejected("unknown symbol")
    sentiment = value["sentiment"]
    confidence = value["confidence"]
    if not isinstance(sentiment, (int, float)) or not math.isfinite(sentiment) or not -1 <= sentiment <= 1:
        raise FeatureRejected("invalid sentiment")
    if not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise FeatureRejected("invalid confidence")
    observed_at = value["observed_at"]
    if not isinstance(observed_at, str) or not observed_at.strip() or _INJECTION.search(observed_at):
        raise FeatureRejected("invalid or injected provenance")
    return NewsFeature(symbol, float(sentiment), float(confidence), observed_at)
