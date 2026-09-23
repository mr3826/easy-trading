from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from trading_platform.backup import _encrypt_payload, encrypt_directory, restore_backup
from trading_platform.chaos_engine import (
    FAILURE_CLOCK_SKEW,
    FAILURE_DB_LOSS,
    FAILURE_DISK_PRESSURE,
    FAILURE_DUPLICATE_EVENT,
    FAILURE_INTERNET_LOSS,
    FAILURE_PROCESS_KILL,
    DeadManHeartbeat,
    FailureInjector,
    FailureScenarios,
)
from trading_platform.data import MarketDataProvider
from trading_platform.domain import Bar, Instrument, OrderSide, OrderType, Signal, TimeInForce
from trading_platform.monitor import (
    AlertHandler,
    FileAlertChannel,
    SystemMonitor,
    TelegramAlertChannel,
)
from trading_platform.persistence.shadow_archive import ShadowArchive
from trading_platform.shadow import PROBLEM_DUPLICATE, PROBLEM_STALE, ShadowOrchestrator

UTC = timezone.utc


def make_bar(symbol: str = "AAPL", day: int = 1, opening: float = 100.0) -> Bar:
    instrument = Instrument(symbol)
    return Bar(instrument, datetime(2026, 1, day, tzinfo=UTC), opening, opening + 2, opening - 1, opening + 1, 1000)


def fixed_now() -> datetime:
    return datetime(2026, 1, 2, tzinfo=UTC)


class FakeProvider(MarketDataProvider):
    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars

    def get_bars(self, instrument: Instrument, start: datetime, end: datetime, session: Any = None) -> list[Bar]:
        return [bar for bar in self.bars if start <= bar.timestamp <= end]

    def has_bars(self, instrument: Instrument, start: datetime, end: datetime) -> bool:
        return bool(self.get_bars(instrument, start, end))

    def get_latest_bar(self, instrument: Instrument) -> Bar | None:
        return self.bars[-1] if self.bars else None

    def get_metadata(self, instrument: Instrument) -> Any:
        raise NotImplementedError


class FakeRiskEngine:
    def __init__(self, approved: bool = True, reason: str | None = None) -> None:
        self.approved = approved
        self.reason = reason
        self.calls: list[str] = []

    def check_order(self, order: Any, positions: Any, cash: float) -> tuple[bool, str | None, Any]:
        self.calls.append(f"{order.instrument.symbol}:{order.quantity}")
        if self.approved:
            return True, None, type("Policy", (), {"version": 1})()
        return False, self.reason or "rejected by fake risk", type("Policy", (), {"version": 1})()


class FlakyStrategy:
    def __init__(self) -> None:
        self.mutated = False

    def __call__(self, bar: Bar) -> Signal | None:
        if self.mutated or bar.instrument.symbol != "AAPL":
            return None
        return Signal(bar.instrument, OrderSide.BUY, 1, None, OrderType.MARKET, TimeInForce.DAY)


def build_orchestrator(bars: list[Bar], archive_dir: Path) -> tuple[ShadowOrchestrator, FakeRiskEngine, FlakyStrategy]:
    strategy = FlakyStrategy()
    risk = FakeRiskEngine()
    archive = ShadowArchive(archive_dir)
    return ShadowOrchestrator(FakeProvider(bars), strategy, risk, archive, now_fn=fixed_now), risk, strategy


# ---------------------------------------------------------------------------
# Real dependency probes
# ---------------------------------------------------------------------------


def test_real_db_probe_fail_closed() -> None:
    class FakeConnection:
        def execute(self, *_args: object) -> None:
            return None

    def healthy_probe() -> object:
        return FakeConnection()

    def broken_probe() -> object:
        raise ConnectionError("connection refused")

    monitor = SystemMonitor(None)
    assert monitor.db_health()["connectivity"] is None

    monitor = SystemMonitor(None, db_probe=healthy_probe)
    assert monitor.db_health()["connectivity"] is True
    monitor.update_last_write_timestamp(datetime.now(timezone.utc))
    assert monitor.db_health()["healthy"] is True

    monitor = SystemMonitor(None, db_probe=broken_probe)
    health = monitor.db_health()
    assert health["connectivity"] is False
    assert health["healthy"] is False
    monitor.update_last_write_timestamp(datetime.now(timezone.utc))
    assert monitor.db_health()["healthy"] is False

    with FailureInjector(FAILURE_DB_LOSS) as injector:
        monitor = SystemMonitor(None, db_probe=injector.wrap(healthy_probe))
        health = monitor.db_health()
    assert health["connectivity"] is False and health["healthy"] is False


def test_storage_probe_real_and_fail_closed(tmp_path: Path) -> None:
    monitor = SystemMonitor(None, disk_probe=shutil.disk_usage, disk_path=tmp_path)
    healthy = monitor.check_storage(min_free_bytes=1.0)
    assert healthy["healthy"] is True and healthy["free_bytes"] > 0

    unhealthy = monitor.check_storage(min_free_bytes=10**18)
    assert unhealthy["healthy"] is False and "below minimum" in str(unhealthy["alert"])

    monitor.disk_probe = None
    fallback = monitor.check_storage(min_free_bytes=1.0, path=tmp_path)
    assert fallback["healthy"] is True

    with FailureInjector(FAILURE_DISK_PRESSURE) as injector:
        monitor.disk_probe = injector.wrap(shutil.disk_usage)
        health = monitor.check_storage(min_free_bytes=1.0)
    assert health["healthy"] is False and "probe failed" in str(health["alert"])


def test_journal_lag_from_real_source() -> None:
    counts: list[tuple[int, int]] = [(5, 2), (9, 9)]
    provider_calls: list[int] = []

    def journal_counts() -> tuple[int, int]:
        provider_calls.append(1)
        return counts[len(provider_calls) - 1]

    monitor = SystemMonitor(None)
    monitor.update_journal_counts(4, 1)
    assert monitor.journal_lag() == 3

    monitor = SystemMonitor(None, journal_counts_provider=journal_counts)
    assert monitor.journal_lag() == 3
    assert monitor.journal_lag() == 0
    assert len(provider_calls) == 2


# ---------------------------------------------------------------------------
# Independent alert channels
# ---------------------------------------------------------------------------


def test_default_alert_channels_are_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    alert_file = tmp_path / "channel-b.log"
    monkeypatch.setattr("trading_platform.monitor.DEFAULT_ALERT_FILE", alert_file)
    handler = AlertHandler()
    assert handler.channel_a is AlertHandler._default_channel_a
    handler.alert("reconciliation mismatch")
    captured = capsys.readouterr()
    assert "[ALERT-CHAN-A] [CRITICAL] reconciliation mismatch" in captured.err
    assert alert_file.exists()
    assert alert_file.read_text(encoding="utf-8") == "[CRITICAL] reconciliation mismatch\n"


def test_independent_file_channels_and_severity(tmp_path: Path) -> None:
    file_a = tmp_path / "a.log"
    file_b = tmp_path / "b.log"
    handler = AlertHandler(FileAlertChannel(file_a).send, FileAlertChannel(file_b).send)
    handler.alert("db unhealthy", severity="WARNING")
    handler.alert("heartbeat missed", severity="EMERGENCY")
    expected = "[WARNING] db unhealthy\n[EMERGENCY] heartbeat missed\n"
    assert file_a.read_text(encoding="utf-8") == expected
    assert file_b.read_text(encoding="utf-8") == expected
    assert file_a != file_b


def test_telegram_channel_config_and_send(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        TelegramAlertChannel("", "chat")
    with pytest.raises(ValueError):
        TelegramAlertChannel("token", "")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(ValueError):
        TelegramAlertChannel.from_env()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    channel = TelegramAlertChannel.from_env()
    assert channel.endpoint() == "https://api.telegram.org/bot123:abc/sendMessage"

    posted: list[dict[str, Any]] = []

    class Response:
        status = 200

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_urlopen(request: Any, timeout: float | None = None) -> Response:
        posted.append({"url": request.full_url, "body": json.loads(request.data.decode("utf-8"))})
        return Response()

    monkeypatch.setattr("trading_platform.monitor.urlopen", fake_urlopen)
    channel.send("critical alert")
    assert posted[0]["url"].startswith("https://api.telegram.org/")
    assert posted[0]["body"] == {"chat_id": "-100", "text": "critical alert"}

    class ErrorResponse(Response):
        status = 500

    monkeypatch.setattr("trading_platform.monitor.urlopen", lambda *_a, **_k: ErrorResponse())
    with pytest.raises(RuntimeError) as exc_info:
        channel.send("critical")
    assert "500" in str(exc_info.value)
    assert "123:abc" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Chaos scenarios against the new injectable dependencies
# ---------------------------------------------------------------------------


def test_chaos_disk_pressure_real_branch() -> None:
    with FailureInjector(FAILURE_DISK_PRESSURE) as injector:
        guarded = injector.wrap(lambda: "normal")
        with pytest.raises(OSError):
            guarded()
    assert not injector.status()["active"]
    guarded = injector.wrap(lambda: "normal")
    assert guarded() == "normal"
    with FailureScenarios.disk_pressure() as active:
        assert active.status()["active"]


def test_chaos_clock_skew_on_monitor_freshness() -> None:
    monitor = SystemMonitor(None)
    monitor.update_last_data_timestamp(datetime.now(timezone.utc) - timedelta(seconds=1800))
    assert monitor.check_data_freshness(max_age_seconds=3600.0)["is_fresh"]
    with FailureInjector(FAILURE_CLOCK_SKEW, clock_skew_seconds=7200.0) as injector:
        monitor.now_fn = injector.wrap(lambda: datetime.now(timezone.utc))
        health = monitor.check_data_freshness(max_age_seconds=3600.0)
    assert health["is_fresh"] is False and health["alert"] is not None


def test_chaos_clock_skew_on_shadow_freshness(tmp_path: Path) -> None:
    bars = [make_bar(day=1)]
    baseline, _risk, _strategy = build_orchestrator(bars, tmp_path / "baseline")
    result = baseline.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
    assert result.problems == [] and len(result.decisions) == 1

    skewed, _risk, _strategy = build_orchestrator(bars, tmp_path / "skewed")
    with FailureInjector(FAILURE_CLOCK_SKEW, clock_skew_seconds=3600.0) as injector:
        skewed.now_fn = injector.wrap(fixed_now)
        result = skewed.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
    assert [problem.problem_type for problem in result.problems] == [PROBLEM_STALE]
    assert result.decisions == []


def test_chaos_duplicate_events_against_shadow_run(tmp_path: Path) -> None:
    bars = [make_bar(day=1), make_bar(day=2)]
    orchestrator, _risk, _strategy = build_orchestrator(bars, tmp_path / "archive")
    with FailureInjector(FAILURE_DUPLICATE_EVENT) as injector:
        guarded_run = injector.wrap(orchestrator.run)
        first = guarded_run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
        second = guarded_run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    assert first is not None and second is not None
    assert len(first.decisions) == 2
    assert [problem.problem_type for problem in second.problems] == [PROBLEM_DUPLICATE, PROBLEM_DUPLICATE]
    assert second.decisions == []


def test_chaos_network_loss_fails_alerts_loudly() -> None:
    handler = AlertHandler()
    with pytest.raises(ConnectionError):
        with FailureInjector(FAILURE_INTERNET_LOSS) as injector:
            handler.channel_a = injector.wrap(handler.channel_a)
            handler.channel_b = injector.wrap(handler.channel_b)
            handler.alert("reconciliation mismatch")


def test_chaos_restart_leaves_archive_replayable(tmp_path: Path) -> None:
    bars = [make_bar(day=1), make_bar(day=2)]
    orchestrator, _risk, _strategy = build_orchestrator(bars, tmp_path / "archive")
    orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))

    with pytest.raises(RuntimeError):
        with FailureInjector(FAILURE_PROCESS_KILL) as injector:
            orchestrator.provider.get_bars = injector.wrap(orchestrator.provider.get_bars)
            orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))

    restarted, _risk, _strategy = build_orchestrator(bars, tmp_path / "archive")
    resumed = restarted.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    assert [problem.problem_type for problem in resumed.problems] == [PROBLEM_DUPLICATE]
    assert len(resumed.decisions) == 1
    assert restarted.verify_replay()["match"] is True
    with FailureScenarios.restart_during_order() as active:
        assert active.status()["failure_type"] == "restart_during_order"


def test_deadman_alert_transport_wired(caplog: pytest.LogCaptureFixture) -> None:
    sent: list[str] = []

    class FakeTransport:
        def send(self, message: str) -> None:
            sent.append(message)

    heartbeat = DeadManHeartbeat(interval=0.001, failure_threshold=1, alert_transport=FakeTransport())
    assert not heartbeat.check_and_alert()
    assert sent and "unhealthy" in sent[0]

    silent = DeadManHeartbeat(interval=0.001, failure_threshold=1)
    with caplog.at_level(logging.WARNING, logger="trading_platform.chaos_engine"):
        assert not silent.check_and_alert()
    assert any("no alert transport" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Backup and restore round trip
# ---------------------------------------------------------------------------


def _craft_archive(entries: list[dict[str, Any]], files: list[tuple[str, bytes]], key: bytes) -> bytes:
    manifest = json.dumps({"version": 1, "files": entries}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in [("_manifest.json", manifest), *files]:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return _encrypt_payload(buffer.getvalue(), key)


def test_backup_restore_round_trip_rebuilds_same_state(tmp_path: Path) -> None:
    bars = [make_bar(day=1), make_bar(day=2)]
    orchestrator, _risk, _strategy = build_orchestrator(bars, tmp_path / "archive")
    orchestrator.run(Instrument("AAPL"), datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC))
    original_decisions = (tmp_path / "archive" / "decisions.jsonl").read_text(encoding="utf-8")

    journal = tmp_path / "journal_export"
    journal.mkdir()
    (journal / "journal.jsonl").write_text(
        json.dumps({"event_id": "e1", "lag": 0}, sort_keys=True)
        + "\n"
        + json.dumps({"event_id": "e2", "lag": 0}, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )

    key = os.urandom(32)
    archive_blob = tmp_path / "shadow-archive.enc"
    journal_blob = tmp_path / "journal-export.enc"
    archive_checksum = encrypt_directory(tmp_path / "archive", archive_blob, key)
    journal_checksum = encrypt_directory(journal, journal_blob, key)

    clean = tmp_path / "clean-room"
    rebuilt_archive = restore_backup(archive_blob, clean / "archive", key, expected_checksum=archive_checksum)
    rebuilt_journal = restore_backup(journal_blob, clean / "journal", key, expected_checksum=journal_checksum)
    assert [path.name for path in rebuilt_archive] == ["decisions.jsonl", "inputs.jsonl"]
    assert [path.name for path in rebuilt_journal] == ["journal.jsonl"]

    restored_archive = clean / "archive"
    assert (restored_archive / "decisions.jsonl").read_text(encoding="utf-8") == original_decisions
    assert ShadowArchive(restored_archive).verify()

    replayed, _risk, _strategy = build_orchestrator(bars, restored_archive)
    outcome = replayed.verify_replay()
    assert outcome["match"] is True and outcome["decisions"] == 2

    events = [
        json.loads(line)
        for line in (clean / "journal" / "journal.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [event["event_id"] for event in events] == ["e1", "e2"]


def test_restore_fails_closed_on_tampered_or_mismatched_archive(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "decisions.jsonl").write_text("row\n", encoding="utf-8")
    key = os.urandom(32)
    blob = tmp_path / "archive.enc"
    good_checksum = encrypt_directory(source, blob, key)

    corrupted = bytearray(blob.read_bytes())
    corrupted[20] ^= 0xFF
    blob.write_bytes(bytes(corrupted))
    with pytest.raises(ValueError):
        restore_backup(blob, tmp_path / "out1", key, expected_checksum=good_checksum)

    fresh_blob = tmp_path / "fresh.enc"
    encrypt_directory(source, fresh_blob, key)
    with pytest.raises(Exception):
        restore_backup(fresh_blob, tmp_path / "out2", os.urandom(32))

    truncated = tmp_path / "truncated.enc"
    truncated.write_bytes(b"short")
    with pytest.raises(ValueError):
        restore_backup(truncated, tmp_path / "out3", key)

    mismatched = tmp_path / "mismatched.enc"
    mismatched.write_bytes(
        _craft_archive([{"name": "d.jsonl", "size": 4, "sha256": "0" * 64}], [("d.jsonl", b"row\n")], key)
    )
    with pytest.raises(ValueError):
        restore_backup(mismatched, tmp_path / "out4", key)

    manifest_mismatch = tmp_path / "manifest-mismatch.enc"
    manifest_mismatch.write_bytes(
        _craft_archive(
            [{"name": "d.jsonl", "size": 4, "sha256": hashlib.sha256(b"row\n").hexdigest()}],
            [("d.jsonl", b"row\n"), ("extra.jsonl", b"x\n")],
            key,
        )
    )
    with pytest.raises(ValueError):
        restore_backup(manifest_mismatch, tmp_path / "out5", key)

    unsafe = tmp_path / "unsafe.enc"
    unsafe.write_bytes(
        _craft_archive(
            [{"name": "../evil", "size": 4, "sha256": hashlib.sha256(b"row\n").hexdigest()}],
            [("../evil", b"row\n")],
            key,
        )
    )
    with pytest.raises(ValueError):
        restore_backup(unsafe, tmp_path / "out6", key)
