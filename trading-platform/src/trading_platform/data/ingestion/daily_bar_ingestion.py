from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from trading_platform.data import (
    CorporateAction,
    CorporateActionProvider,
    CorporateActionType,
    DataMetadata,
    MarketDataProvider,
    validate_bar,
    validate_data_integrity,
)
from trading_platform.domain import Bar, Instrument


def _fallback_checksum(bars: List[Bar]) -> str:
    payload = json.dumps(
        [
            [bar.instrument.symbol, bar.timestamp.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume]
            for bar in bars
        ],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class IngestionResult:
    """Result of a data ingestion operation."""

    def __init__(
        self,
        success: bool,
        bars: List[Bar] | None = None,
        metadata: DataMetadata | None = None,
        errors: List[str] | None = None,
        warnings: List[str] | None = None,
        raw_archive_path: Path | None = None,
    ):
        self.success = success
        self.bars = bars or []
        self.raw_archive_path = raw_archive_path
        if metadata is not None:
            self.metadata = metadata
        else:
            self.metadata = DataMetadata(
                source="unknown",
                schema_version="0.0.0",
                dataset_version="0.0.0",
                retrieval_timestamp=datetime.now(timezone.utc),
                checksum=_fallback_checksum(self.bars),
            )
        self.errors = errors or []
        self.warnings = warnings or []


class DailyBarIngestion:
    """Handles daily bar ingestion from Parquet files with validation and provenance.

    Raw vendor records are archived separately from normalized data under
    ``raw_archive_dir`` (both traceable via metadata: source, checksum,
    retrieval timestamp). Corporate-action adjustments are applied only through
    an explicit ``CorporateActionProvider`` and are point-in-time.
    """

    def __init__(
        self,
        data_provider: MarketDataProvider,
        corporate_action_provider: CorporateActionProvider | None = None,
        raw_archive_dir: Path | None = None,
    ):
        self.data_provider = data_provider
        self.corporate_action_provider = corporate_action_provider
        self.raw_archive_dir = raw_archive_dir
        self._universe: set[Instrument] | None = None  # Engineering universe
        self._research_universe: set[Instrument] | None = None

    def set_engineering_universe(self, universe: set[Instrument]) -> None:
        """Set the engineering universe - a small fixed list for plumbing tests."""
        self._universe = universe

    def set_research_universe(self, universe: set[Instrument]) -> None:
        """Set the research universe - point-in-time membership including removed/delisted names."""
        self._research_universe = universe

    def set_raw_archive_dir(self, raw_archive_dir: Path) -> None:
        """Set the raw archive directory distinct from normalized Parquet storage."""
        self.raw_archive_dir = raw_archive_dir

    def ingest_symbol(
        self, symbol: str, start: datetime, end: datetime, as_of: datetime | None = None
    ) -> IngestionResult:
        """Ingest daily bars for a single symbol within the date range.

        When ``as_of`` is provided, only bars with available_at <= as_of are
        ingested (point-in-time mode). Returns IngestionResult with bars,
        metadata, and any errors/warnings.
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

        if as_of is not None:
            bars = self.data_provider.get_bars(instrument, start, end, as_of=as_of)
        else:
            bars = self.data_provider.get_bars(instrument, start, end)

        # Symbol-history boundary: a symbol's history never includes data
        # under a different symbol mapping.
        foreign_symbols = sorted({bar.instrument.symbol for bar in bars} - {instrument.symbol})
        if foreign_symbols:
            return IngestionResult(
                success=False,
                errors=[
                    f"Symbol-history boundary violated for {symbol}: bars present under {', '.join(foreign_symbols)}"
                ],
            )

        raw_archive_path = self._write_raw_archive(instrument, bars)

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

        # Apply corporate actions point-in-time (splits and dividends)
        if self.corporate_action_provider is not None:
            splits = self.corporate_action_provider.get_splits(instrument, start, end)
            dividends = self.corporate_action_provider.get_dividends(instrument, start, end)
            valid_bars = apply_split_adjustment(valid_bars, splits, end)
            valid_bars = apply_dividend_adjustment(valid_bars, dividends, end)

        metadata = self.data_provider.get_metadata(instrument)

        return IngestionResult(
            success=not validation_errors,
            bars=valid_bars,
            metadata=metadata,
            errors=validation_errors,
            warnings=validation_warnings,
            raw_archive_path=raw_archive_path,
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

    def _write_raw_archive(self, instrument: Instrument, bars: List[Bar]) -> Path | None:
        """Append as-received vendor records to the raw archive with provenance."""
        if self.raw_archive_dir is None or not bars:
            return None
        metadata = self.data_provider.get_metadata(instrument)
        symbol_dir = self.raw_archive_dir / f"symbol={instrument.symbol}"
        symbol_dir.mkdir(parents=True, exist_ok=True)
        index = len(list(symbol_dir.glob("raw-*.jsonl"))) + 1
        path = symbol_dir / f"raw-{index:04d}-{metadata.dataset_version}.jsonl"
        lines: List[str] = []
        for bar in bars:
            payload: Dict[str, Any] = {
                "symbol": bar.instrument.symbol,
                "timestamp": bar.timestamp.isoformat(),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            record = {
                **payload,
                "available_at": (bar.available_at or bar.timestamp).isoformat(),
                "source": metadata.source,
                "dataset_version": metadata.dataset_version,
                "retrieval_timestamp": metadata.retrieval_timestamp.isoformat(),
                "checksum": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            }
            lines.append(json.dumps(record, sort_keys=True))
        path.write_text("\n".join(lines))
        return path


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


def read_raw_archive(raw_archive_dir: Path, symbol: str) -> List[Dict[str, Any]]:
    """Read raw archived vendor records for a symbol for traceability."""
    symbol_dir = raw_archive_dir / f"symbol={symbol}"
    if not symbol_dir.is_dir():
        raise FileNotFoundError(f"Raw archive not found for {symbol}: {symbol_dir}")
    records: List[Dict[str, Any]] = []
    for path in sorted(symbol_dir.glob("raw-*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def apply_split_adjustment(
    bars: List[Bar],
    splits: List[CorporateAction],
    reference_timestamp: datetime,
) -> List[Bar]:
    """Apply split adjustments to bar prices.

    Bars strictly before a split's ex_date are divided by that split's ratio
    (backward adjustment); bars on or after the ex_date already trade at
    post-split prices and are left untouched. Volume is not adjusted.
    """
    applicable_splits = [
        s
        for s in splits
        if s.ex_date <= reference_timestamp
        and s.action_type == CorporateActionType.SPLIT
        and s.ratio is not None
        and s.ratio > 0
    ]
    if not applicable_splits:
        return bars

    adjusted_bars = []
    for bar in bars:
        adjustment_factor = 1.0
        for split in applicable_splits:
            ratio = split.ratio
            if ratio is not None and ratio > 0 and bar.timestamp < split.ex_date:
                adjustment_factor *= ratio
        if adjustment_factor == 1.0:
            adjusted_bars.append(bar)
            continue
        adjusted_bars.append(
            Bar(
                instrument=bar.instrument,
                timestamp=bar.timestamp,
                open=bar.open / adjustment_factor,
                high=bar.high / adjustment_factor,
                low=bar.low / adjustment_factor,
                close=bar.close / adjustment_factor,
                volume=bar.volume,
                session=bar.session,
                available_at=bar.available_at,
            )
        )
    return adjusted_bars


def apply_dividend_adjustment(
    bars: List[Bar],
    dividends: List[CorporateAction],
    reference_timestamp: datetime,
) -> List[Bar]:
    """Apply dividend adjustments to bar prices.

    Bars strictly before a dividend's ex_date are scaled by
    ``1 - cash_amount / bar.close`` (proportional back-adjustment; for the
    close this equals ``close - cash`` so holding-period returns stay
    continuous across the ex-date). Bars on or after the ex_date are left
    untouched. Bars whose scaled prices would turn non-positive keep their
    original prices so the adjusted series stays constructible.
    """
    applicable_dividends = [
        d
        for d in dividends
        if d.ex_date <= reference_timestamp
        and d.action_type == CorporateActionType.DIVIDEND
        and d.cash_amount is not None
        and d.cash_amount > 0
    ]
    if not applicable_dividends:
        return bars

    adjusted_bars = []
    for bar in bars:
        adjustment_factor = 1.0
        for dividend in applicable_dividends:
            cash_amount = dividend.cash_amount
            if (
                cash_amount is not None
                and cash_amount > 0
                and cash_amount < bar.close
                and bar.timestamp < dividend.ex_date
            ):
                adjustment_factor *= 1.0 - cash_amount / bar.close
        if adjustment_factor == 1.0:
            adjusted_bars.append(bar)
            continue
        adjusted_bars.append(
            Bar(
                instrument=bar.instrument,
                timestamp=bar.timestamp,
                open=bar.open * adjustment_factor,
                high=bar.high * adjustment_factor,
                low=bar.low * adjustment_factor,
                close=bar.close * adjustment_factor,
                volume=bar.volume,
                session=bar.session,
                available_at=bar.available_at,
            )
        )
    return adjusted_bars
