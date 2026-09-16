from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import pandas as pd

from trading_platform.data import (
    CorporateAction,
    CorporateActionType,
    DataMetadata,
    MarketDataProvider,
    validate_bar,
    validate_data_integrity,
)
from trading_platform.domain import Bar, Instrument


class IngestionResult:
    """Result of a data ingestion operation."""

    def __init__(
        self,
        success: bool,
        bars: List[Bar] | None = None,
        metadata: DataMetadata | None = None,
        errors: List[str] | None = None,
        warnings: List[str] | None = None,
    ):
        self.success = success
        self.bars = bars or []
        self.metadata = metadata or DataMetadata(
            source="unknown",
            schema_version="0.0.0",
            dataset_version="0.0.0",
            retrieval_timestamp=datetime.now(timezone.utc),
            checksum="",
        )
        self.errors = errors or []
        self.warnings = warnings or []


class DailyBarIngestion:
    """Handles daily bar ingestion from Parquet files with validation and provenance."""

    def __init__(self, data_provider: MarketDataProvider):
        self.data_provider = data_provider
        self._universe: set[Instrument] | None = None  # Engineering universe
        self._research_universe: set[Instrument] | None = None

    def set_engineering_universe(self, universe: set[Instrument]) -> None:
        """Set the engineering universe - a small fixed list for plumbing tests."""
        self._universe = universe

    def set_research_universe(self, universe: set[Instrument]) -> None:
        """Set the research universe - point-in-time membership including removed/delisted names."""
        self._research_universe = universe

    def ingest_symbol(self, symbol: str, start: datetime, end: datetime) -> IngestionResult:
        """Ingest daily bars for a single symbol within the date range.

        Returns IngestionResult with bars, metadata, and any errors/warnings.
        """
        instrument = Instrument(symbol=symbol)

        # Check universe membership
        if self._universe is not None and instrument not in self._universe:
            return IngestionResult(
                success=False,
                errors=[f"Symbol {symbol} not in engineering universe"],
            )

        # Check if data exists
        if not self.data_provider.has_bars(instrument, start, end):
            return IngestionResult(
                success=False,
                errors=[f"No data available for {symbol} in range [{start}, {end}]"],
            )

        bars = self.data_provider.get_bars(instrument, start, end)

        # Validate bar integrity
        validation_errors: List[str] = []
        validation_warnings: List[str] = []
        valid_bars: List[Bar] = []
        for bar in bars:
            is_valid, error = validate_bar(bar)
            if is_valid:
                valid_bars.append(bar)
            elif error is not None:
                validation_errors.append(error)

        # Validate overall data integrity
        integrity_ok, integrity_errors = validate_data_integrity(valid_bars)
        if not integrity_ok:
            validation_warnings.extend(integrity_errors)

        # Apply corporate actions if needed
        # ... (future: adjust bars for splits/dividends)

        metadata = self.data_provider.get_metadata(instrument)

        return IngestionResult(
            success=not validation_errors,
            bars=valid_bars,
            metadata=metadata,
            errors=validation_errors,
            warnings=validation_warnings,
        )

    def ingest_universe(self, symbols: List[str], start: datetime, end: datetime) -> Dict[str, IngestionResult]:
        """Ingest bars for multiple symbols.

        Returns dict mapping symbol -> IngestionResult.
        """
        results: Dict[str, IngestionResult] = {}
        for symbol in symbols:
            result = self.ingest_symbol(symbol, start, end)
            results[symbol] = result
        return results


def load_parquet_data(data_dir: Path, symbol: str) -> pd.DataFrame:
    """Load daily bar data from a Parquet file.

    Expected Parquet columns: symbol, timestamp, open, high, low, close, volume
    Timestamp column should be datetime64[ns, UTC].
    """
    parquet_path = data_dir / f"{symbol}.parquet"
    if not parquet_path.exists():
        raise FileNotFoundError(f"Parquet data not found: {parquet_path}")

    df = pd.read_parquet(parquet_path)

    # Ensure timestamp column is datetime with UTC timezone
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    # Ensure numeric columns
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Sort by timestamp
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp")

    return df


def apply_split_adjustment(
    bars: List[Bar],
    splits: List[CorporateAction],
    reference_timestamp: datetime,
) -> List[Bar]:
    """Apply split adjustments to bar prices.

    Adjusts open, high, low, close prices backward-adjustively
    based on splits that occurred before the reference timestamp.

    For a 2-for-1 split: pre-split prices are divided by 2.
    """
    if not splits:
        return bars

    # Find splits that occurred before the reference timestamp
    applicable_splits = [
        s for s in splits if s.ex_date <= reference_timestamp and s.action_type == CorporateActionType.SPLIT
    ]

    if not applicable_splits:
        return bars

    # Calculate cumulative adjustment factor
    # For each split with ratio R, pre-split price = post-split price * R
    # So we divide pre-split prices by the product of all ratios
    adjustment_factor = 1.0
    for split in sorted(applicable_splits, key=lambda s: s.ex_date):
        if split.ratio is not None and split.ratio != 0:
            adjustment_factor *= split.ratio

    if adjustment_factor == 1.0:
        return bars

    # Adjust prices: pre-split price = post-split price * adjustment_factor
    # So to go backward: adjusted = original / adjustment_factor
    adjusted_bars = []
    for bar in bars:
        adjusted_bar = Bar(
            instrument=bar.instrument,
            timestamp=bar.timestamp,
            open=bar.open / adjustment_factor if bar.open is not None else None,
            high=bar.high / adjustment_factor if bar.high is not None else None,
            low=bar.low / adjustment_factor if bar.low is not None else None,
            close=bar.close / adjustment_factor if bar.close is not None else None,
            volume=bar.volume,  # Volume is NOT adjusted for splits
            session=bar.session,
        )
        adjusted_bars.append(adjusted_bar)

    return adjusted_bars


def apply_dividend_adjustment(
    bars: List[Bar],
    dividends: List[CorporateAction],
    reference_timestamp: datetime,
) -> List[Bar]:
    """Apply dividend adjustments to bar prices.

    For simplicity in V1, dividend adjustment is noted but not
    automatically applied to price fields. The cash amount is
    tracked separately for cost basis calculations.
    """
    # V1: Dividend handling is documented but not price-adjusted
    # Future: could adjust close prices by dividend yield
    return bars
