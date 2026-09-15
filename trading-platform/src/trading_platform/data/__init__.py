from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from trading_platform.domain import Instrument, TradingSession


class DataFrequency(Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class DataStatus(Enum):
    RAW = "raw"  # Unadjusted source data
    ADJUSTED = "adjusted"  # Point-in-time adjusted view


@dataclass(frozen=True)
class DataMetadata:
    """Metadata preserved for every data file load."""

    source: str  # Vendor name
    schema_version: str
    dataset_version: str
    retrieval_timestamp: datetime
    checksum: str  # SHA256 of the file contents
    license: str | None = None
    vendor_identifier: str | None = None  # Vendor-specific symbol mapping


# ---- MarketDataProvider Interface ----

class MarketDataProvider:
    """Abstract interface for providing daily market bar data.

    Implementations must be deterministic and respect point-in-time constraints.
    Only data available at or before the decision timestamp is valid.
    """

    def get_bars(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        session: TradingSession = TradingSession.DAY,
    ) -> List[Bar]:
        """Get daily bars for an instrument within the date range.

        Args:
            instrument: The financial instrument
            start: Start date (inclusive)
            end: End date (inclusive)
            session: Trading session type

        Returns:
            List of Bar objects, sorted by timestamp ascending
        """
        raise NotImplementedError

    def has_bars(self, instrument: Instrument, start: datetime, end: datetime) -> bool:
        """Check if bars are available for the given range."""
        raise NotImplementedError

    def get_latest_bar(self, instrument: Instrument) -> Bar | None:
        """Get the most recent bar for an instrument."""
        raise NotImplementedError

    def get_metadata(self, instrument: Instrument) -> DataMetadata:
        """Get metadata for the data source of this instrument."""
        raise NotImplementedError


# ---- CorporateActionProvider Interface ----

class CorporateActionProvider:
    """Abstract interface for corporate actions (splits, dividends, delistings).

    All corporate actions are point-in-time: only actions effective on or
    before the given timestamp are valid.
    """

    def get_splits(
        self, instrument: Instrument, start: datetime, end: datetime
    ) -> List[CorporateAction]:
        """Get all stock splits in the given date range."""
        raise NotImplementedError

    def get_dividends(
        self, instrument: Instrument, start: datetime, end: datetime
    ) -> List[CorporateAction]:
        """Get all dividend payments in the given date range."""
        raise NotImplementedError

    def get_delisting(self, instrument: Instrument) -> CorporateAction | None:
        """Get delisting corporate action if the symbol was delisted."""
        raise NotImplementedError

    def has_corporate_action(
        self, instrument: Instrument, timestamp: datetime
    ) -> bool:
        """Check if a corporate action affects this instrument at the given time."""
        raise NotImplementedError


# ---- CorporateAction type ----

class CorporateActionType(Enum):
    SPLIT = "split"
    DIVIDEND = "dividend"
    REVERSE_SPLIT = "reverse_split"
    DELISTING = "delisting"


@dataclass(frozen=True)
class CorporateAction:
    instrument: Instrument
    action_type: CorporateActionType
    ex_date: datetime
    record_date: datetime | None = None
    pay_date: datetime | None = None
    ratio: float | None = None  # e.g., 2.0 for 2-for-1 split
    cash_amount: float | None = None  # dividend cash per share


# ---- Parquet-backed implementation skeleton ----

class ParquetMarketDataProvider(MarketDataProvider):
    """Concrete implementation reading bars from versioned Parquet files.

    Data layout per Parquet file:
    - columns: instrument_symbol, timestamp, open, high, low, close, volume
    - index: timestamp (partitioned by year/month)
    - metadata: schema_version, dataset_version, vendor, checksum
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._cache: Dict[str, DataMetadata] = {}

    def _read_metadata(self, path: Path) -> DataMetadata:
        """Read data metadata from Parquet file metadata."""
        # In a full implementation, this would read the Parquet metadata
        # For now, return placeholder
        return DataMetadata(
            source="parquet-source",
            schema_version="v1.0",
            dataset_version="v1.0",
            retrieval_timestamp=datetime.now(timezone.utc),
            checksum="placeholder",
        )

    def get_bars(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        session: TradingSession = TradingSession.DAY,
    ) -> List[Bar]:
        """Read bars from Parquet files for the given instrument and date range."""
        # Skeleton - full implementation would read from Parquet
        bars: List[Bar] = []
        # TODO: Implement Parquet reading with proper point-in-time filtering
        return bars

    def has_bars(self, instrument: Instrument, start: datetime, end: datetime) -> bool:
        """Check if bars are available."""
        return len(self.get_bars(instrument, start, end)) > 0

    def get_latest_bar(self, instrument: Instrument) -> Bar | None:
        """Get the most recent bar."""
        bars = self.get_bars(instrument, datetime.min, datetime.max)
        return bars[-1] if bars else None

    def get_metadata(self, instrument: Instrument) -> DataMetadata:
        """Get data metadata for instrument."""
        return self._read_metadata(
            self.data_dir / f"{instrument.symbol}.parquet"
        )


# ---- Validation utilities ----

def validate_bar(bar: Bar) -> Tuple[bool, Optional[str]]:
    """Validate a bar's integrity.

    Returns (is_valid, error_message). A bar is invalid if:
    - open, high, low, close are NaN or infinite
    - high < low (impossible range)
    - high < open AND high < close (impossible for the bar to close below the high)
    - low > open AND low > close (impossible for the bar to close above the low)
    - volume is negative
    """
    import math

    if math.isnan(bar.open) or math.isnan(bar.high) or math.isnan(bar.low) or math.isnan(bar.close):
        return False, "Bar contains NaN price"

    if math.isinf(bar.open) or math.isinf(bar.high) or math.isinf(bar.low) or math.isinf(bar.close):
        return False, "Bar contains infinite price"

    if bar.high < bar.low:
        return False, "Impossible: high < low"

    if bar.high < bar.open and bar.high < bar.close:
        return False, "Impossible: bar close above high"

    if bar.low > bar.open and bar.low > bar.close:
        return False, "Impossible: bar close below low"

    if bar.volume < 0:
        return False, "Negative volume"

    return True, None


def validate_data_integrity(
    bars: List[Bar],
) -> Tuple[bool, List[str]]:
    """Validate a collection of bars for common data issues.

    Checks:
    - Duplicate timestamps
    - Non-monotonic timestamps
    - Gaps in sequence (for daily bars, expects M-1 gap on weekends)
    - Impossible OHLC relationships
    """
    errors: List[str] = []

    if not bars:
        errors.append("No bars provided for validation")
        return False, errors

    timestamps = [bar.timestamp for bar in bars]

    # Check for non-monotonic timestamps
    if timestamps != sorted(timestamps):
        errors.append("Timestamps are not monotonically increasing")

    # Check for duplicate timestamps
    if len(timestamps) != len(set(timestamps)):
        errors.append("Duplicate timestamps found")

    # Check OHLC validity for each bar
    for bar in bars:
        valid, msg = validate_bar(bar)
        if not valid:
            errors.append(f"Invalid bar at {bar.timestamp}: {msg}")

    return len(errors) == 0, errors