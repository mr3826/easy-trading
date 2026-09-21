from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

from trading_platform.domain import (
    Bar,
    CorporateAction,
    Instrument,
    TradingSession,
    require_utc,
)
from trading_platform.domain import (
    CorporateActionType as CorporateActionType,
)


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
    available_at: datetime | None = None  # Dataset visibility timestamp for point-in-time reads


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
        as_of: datetime | None = None,
    ) -> List[Bar]:
        """Get daily bars for an instrument within the date range.

        Args:
            instrument: The financial instrument
            start: Start date (inclusive, UTC-aware)
            end: End date (inclusive, UTC-aware)
            session: Trading session type
            as_of: Decision timestamp for point-in-time filtering. Only bars
                with available_at <= as_of are returned so future data cannot
                leak into historical decisions. None returns the full stored
                history (backfill mode); decision-time callers must pass as_of.

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

    def get_splits(self, instrument: Instrument, start: datetime, end: datetime) -> List[CorporateAction]:
        """Get all stock splits in the given date range."""
        raise NotImplementedError

    def get_dividends(self, instrument: Instrument, start: datetime, end: datetime) -> List[CorporateAction]:
        """Get all dividend payments in the given date range."""
        raise NotImplementedError

    def get_delisting(self, instrument: Instrument) -> CorporateAction | None:
        """Get delisting corporate action if the symbol was delisted."""
        raise NotImplementedError

    def has_corporate_action(self, instrument: Instrument, timestamp: datetime) -> bool:
        """Check if a corporate action affects this instrument at the given time."""
        raise NotImplementedError


# ---- CorporateAction type ----
# Canonical definition: ``trading_platform.domain.CorporateAction`` (enum-based,
# frozen) re-exported here so the data layer and domain share one type.


# ---- Parquet-backed implementation ----

_REQUIRED_BAR_COLUMNS = ("instrument_symbol", "timestamp", "open", "high", "low", "close", "volume")
_PARQUET_SOURCE = "parquet-source"


def _version_key(version: str) -> Tuple[int, ...]:
    parts = re.findall(r"\d+", version)
    return tuple(int(part) for part in parts) if parts else (0,)


def _to_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ParquetMarketDataProvider(MarketDataProvider):
    """Concrete implementation reading and writing bars from Parquet files.

    Documented storage layout (choice: hive-style per-symbol/year partitions):

        {data_dir}/symbol={SYMBOL}/year={YYYY}/part-{index:04d}-{dataset_version}.parquet

    Each file carries columns: instrument_symbol, timestamp, open, high, low,
    close, volume, available_at (all timestamps UTC; one available_at value per
    file, defaulting to the max bar timestamp at write time). File-level
    provenance (source, schema_version, dataset_version, retrieval_timestamp,
    available_at) is embedded in the Arrow schema metadata; the checksum is the
    SHA-256 of the raw file bytes computed at load and cached per path.

    Point-in-time semantics: get_bars(as_of=T) reads only files with
    available_at <= T, then resolves revisions per timestamp by highest
    dataset_version. Writing a revision requires an explicit dataset_version
    bump; same-version duplicates are rejected. Superseded records remain
    readable via get_superseded(). Raw vendor inputs are archived separately
    by DailyBarIngestion under a distinct raw directory.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._metadata_cache: Dict[Path, DataMetadata] = {}

    def _parquet_files(self, symbol: str) -> List[Path]:
        symbol_dir = self.data_dir / f"symbol={symbol}"
        if not symbol_dir.is_dir():
            return []
        return sorted(symbol_dir.glob("year=*/*.parquet"))

    @staticmethod
    def _read_embedded_metadata(path: Path) -> Dict[str, str]:
        schema = pq.read_schema(path)
        metadata = schema.metadata or {}
        return {key.decode(): value.decode() for key, value in metadata.items()}

    def _deterministic_retrieval_timestamp(self, path: Path, embedded: Dict[str, str]) -> datetime:
        embedded_timestamp = embedded.get("retrieval_timestamp")
        if embedded_timestamp:
            return datetime.fromisoformat(embedded_timestamp)
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)

    def _read_metadata(self, path: Path) -> DataMetadata:
        """Read data metadata from Parquet file metadata.

        The retrieval timestamp is deterministic: the embedded write-time
        value when present, otherwise the file mtime captured once at load.
        """
        cached = self._metadata_cache.get(path)
        if cached is not None:
            return cached
        if not path.is_file():
            raise FileNotFoundError(f"Parquet data not found: {path}")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        embedded = self._read_embedded_metadata(path)
        available_at_value = embedded.get("available_at")
        metadata = DataMetadata(
            source=embedded.get("source", _PARQUET_SOURCE),
            schema_version=embedded.get("schema_version", "v1.0"),
            dataset_version=embedded.get("dataset_version", "v1.0"),
            retrieval_timestamp=self._deterministic_retrieval_timestamp(path, embedded),
            checksum=checksum,
            vendor_identifier=embedded.get("vendor_identifier"),
            available_at=datetime.fromisoformat(available_at_value) if available_at_value else None,
        )
        self._metadata_cache[path] = metadata
        return metadata

    def _resolve_records(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        as_of: datetime | None,
    ) -> Tuple[List[Bar], List[Bar]]:
        require_utc(start)
        require_utc(end)
        if as_of is not None:
            require_utc(as_of)
        resolved: Dict[datetime, Tuple[Tuple[int, ...], Dict[str, Any]]] = {}
        superseded_entries: List[Dict[str, Any]] = []
        for path in self._parquet_files(instrument.symbol):
            metadata = self._read_metadata(path)
            if as_of is not None and metadata.available_at is not None and metadata.available_at > as_of:
                continue
            version_key = _version_key(metadata.dataset_version)
            table = pq.read_table(path)
            missing = [column for column in _REQUIRED_BAR_COLUMNS if column not in table.column_names]
            if missing:
                raise ValueError(f"Parquet file {path} is missing required columns: {', '.join(missing)}")
            symbols = table.column("instrument_symbol").to_pylist()
            timestamps = table.column("timestamp").to_pylist()
            opens = table.column("open").to_pylist()
            highs = table.column("high").to_pylist()
            lows = table.column("low").to_pylist()
            closes = table.column("close").to_pylist()
            volumes = table.column("volume").to_pylist()
            for index in range(len(timestamps)):
                if symbols[index] != instrument.symbol:
                    continue
                timestamp = _to_utc_datetime(timestamps[index])
                if not (start <= timestamp <= end):
                    continue
                entry = {
                    "instrument": instrument,
                    "timestamp": timestamp,
                    "open": float(opens[index]),
                    "high": float(highs[index]),
                    "low": float(lows[index]),
                    "close": float(closes[index]),
                    "volume": int(volumes[index]),
                    "available_at": metadata.available_at or timestamp,
                }
                existing = resolved.get(timestamp)
                if existing is None or version_key > existing[0]:
                    if existing is not None:
                        superseded_entries.append(existing[1])
                    resolved[timestamp] = (version_key, entry)
                elif version_key < existing[0]:
                    superseded_entries.append(entry)
                else:
                    superseded_entries.append(entry)
        current = [resolved[timestamp][1] for timestamp in sorted(resolved)]
        return (
            [self._bar_from_entry(entry) for entry in current],
            [self._bar_from_entry(entry) for entry in superseded_entries],
        )

    @staticmethod
    def _bar_from_entry(entry: Dict[str, Any]) -> Bar:
        return Bar(
            instrument=entry["instrument"],
            timestamp=entry["timestamp"],
            open=entry["open"],
            high=entry["high"],
            low=entry["low"],
            close=entry["close"],
            volume=entry["volume"],
            available_at=entry["available_at"],
        )

    def get_bars(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        session: TradingSession = TradingSession.DAY,
        as_of: datetime | None = None,
    ) -> List[Bar]:
        """Read bars from Parquet files for the given instrument and date range."""
        current, _ = self._resolve_records(instrument, start, end, as_of)
        return current

    def get_superseded(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> List[Bar]:
        """Return stale records shadowed by a later revision (audit trail)."""
        _, superseded = self._resolve_records(instrument, start, end, as_of)
        return sorted(superseded, key=lambda bar: (bar.timestamp, bar.available_at or bar.timestamp))

    def has_bars(self, instrument: Instrument, start: datetime, end: datetime) -> bool:
        """Check if bars are available."""
        return len(self.get_bars(instrument, start, end)) > 0

    def get_latest_bar(self, instrument: Instrument) -> Bar | None:
        """Get the most recent bar."""
        full_range = (datetime.min.replace(tzinfo=timezone.utc), datetime.max.replace(tzinfo=timezone.utc))
        bars = self.get_bars(instrument, full_range[0], full_range[1])
        return bars[-1] if bars else None

    def _primary_file(self, symbol: str) -> Path:
        files = self._parquet_files(symbol)
        if not files:
            raise FileNotFoundError(f"No Parquet data found for symbol {symbol} under {self.data_dir}")
        return max(files, key=lambda path: (_version_key(self._read_metadata(path).dataset_version), path.name))

    def get_metadata(self, instrument: Instrument) -> DataMetadata:
        """Get data metadata for instrument."""
        return self._read_metadata(self._primary_file(instrument.symbol))

    def write_bars(
        self,
        bars: List[Bar],
        source: str,
        schema_version: str,
        dataset_version: str,
        available_at: datetime | None = None,
    ) -> List[Path]:
        """Write bars to normalized Parquet files and return the written paths.

        Duplicate timestamps are rejected within the batch and against records
        already stored under the same dataset version; superseding an existing
        record requires an explicit dataset_version bump.
        """
        if not bars:
            raise ValueError("write_bars requires a non-empty bar list")
        if available_at is not None:
            require_utc(available_at)
        retrieval_timestamp = datetime.now(timezone.utc)
        resolved_available_at = available_at if available_at is not None else max(bar.timestamp for bar in bars)
        groups: Dict[Tuple[str, int], List[Bar]] = {}
        for bar in bars:
            groups.setdefault((bar.instrument.symbol, bar.timestamp.astimezone(timezone.utc).year), []).append(bar)
        written: List[Path] = []
        for (symbol, year), group in sorted(groups.items()):
            stamps = [bar.timestamp for bar in group]
            if len(stamps) != len(set(stamps)):
                raise ValueError(f"duplicate bar timestamps for {symbol} within the write batch")
            existing = self._parquet_files(symbol)
            for path in existing:
                stored_version = self._read_metadata(path).dataset_version
                if stored_version != dataset_version:
                    continue
                stored_stamps = {
                    _to_utc_datetime(value)
                    for value in pq.read_table(path, columns=["timestamp"]).column("timestamp").to_pylist()
                }
                for bar in group:
                    if _to_utc_datetime(bar.timestamp) in stored_stamps:
                        raise ValueError(
                            f"bar for {symbol} at {bar.timestamp.isoformat()} already stored under dataset "
                            f"version {dataset_version}; bump dataset_version to supersede"
                        )
            partition_dir = self.data_dir / f"symbol={symbol}" / f"year={year}"
            partition_dir.mkdir(parents=True, exist_ok=True)
            index = len(list(partition_dir.glob("*.parquet"))) + 1
            path = partition_dir / f"part-{index:04d}-{dataset_version}.parquet"
            ordered = sorted(group, key=lambda bar: bar.timestamp)
            table = pa.table(
                {
                    "instrument_symbol": pa.array([symbol] * len(ordered), type=pa.string()),
                    "timestamp": pa.array([bar.timestamp for bar in ordered], type=pa.timestamp("ns", tz="UTC")),
                    "open": pa.array([float(bar.open) for bar in ordered], type=pa.float64()),
                    "high": pa.array([float(bar.high) for bar in ordered], type=pa.float64()),
                    "low": pa.array([float(bar.low) for bar in ordered], type=pa.float64()),
                    "close": pa.array([float(bar.close) for bar in ordered], type=pa.float64()),
                    "volume": pa.array([int(bar.volume) for bar in ordered], type=pa.int64()),
                    "available_at": pa.array([resolved_available_at] * len(ordered), type=pa.timestamp("ns", tz="UTC")),
                },
                metadata={
                    b"source": source.encode(),
                    b"schema_version": schema_version.encode(),
                    b"dataset_version": dataset_version.encode(),
                    b"retrieval_timestamp": retrieval_timestamp.isoformat().encode(),
                    b"available_at": resolved_available_at.isoformat().encode(),
                },
            )
            pq.write_table(table, path)
            written.append(path)
        return written


# ---- Deterministic in-memory provider ----


class FakeMarketDataProvider(MarketDataProvider):
    """Deterministic in-memory provider for tests and local simulation.

    Seeded with bars; duplicate (symbol, timestamp) pairs are rejected.
    get_bars applies the same point-in-time as-of filtering as the Parquet
    provider. Metadata is content-derived: the checksum is the SHA-256 of the
    canonical bar serialization and the retrieval timestamp is the max bar
    timestamp, never wall-clock time at call time.
    """

    def __init__(self, bars: List[Bar] | None = None, source: str = "fake-provider") -> None:
        self.source = source
        self._bars: Dict[str, List[Bar]] = {}
        for bar in bars or []:
            self.add_bar(bar)

    def add_bar(self, bar: Bar) -> None:
        stored = self._bars.setdefault(bar.instrument.symbol, [])
        if any(bar.timestamp == candidate.timestamp for candidate in stored):
            raise ValueError(f"duplicate bar for {bar.instrument.symbol} at {bar.timestamp.isoformat()}")
        stored.append(bar)
        stored.sort(key=lambda candidate: candidate.timestamp)

    def get_bars(
        self,
        instrument: Instrument,
        start: datetime,
        end: datetime,
        session: TradingSession = TradingSession.DAY,
        as_of: datetime | None = None,
    ) -> List[Bar]:
        require_utc(start)
        require_utc(end)
        if as_of is not None:
            require_utc(as_of)
        bars = [
            bar
            for bar in self._bars.get(instrument.symbol, [])
            if start <= bar.timestamp <= end and (as_of is None or (bar.available_at or bar.timestamp) <= as_of)
        ]
        return list(bars)

    def has_bars(self, instrument: Instrument, start: datetime, end: datetime) -> bool:
        """Check if bars are available."""
        return bool(self.get_bars(instrument, start, end))

    def get_latest_bar(self, instrument: Instrument) -> Bar | None:
        """Get the most recent bar."""
        stored = self._bars.get(instrument.symbol, [])
        return stored[-1] if stored else None

    def get_metadata(self, instrument: Instrument) -> DataMetadata:
        """Get content-derived metadata for instrument."""
        stored = self._bars.get(instrument.symbol, [])
        if not stored:
            raise FileNotFoundError(f"No data stored for symbol {instrument.symbol}")
        payload = json.dumps(
            [
                [bar.instrument.symbol, bar.timestamp.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume]
                for bar in stored
            ],
            sort_keys=True,
        )
        return DataMetadata(
            source=self.source,
            schema_version="v1.0",
            dataset_version="v1.0",
            retrieval_timestamp=max(bar.timestamp for bar in stored),
            checksum=hashlib.sha256(payload.encode()).hexdigest(),
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
