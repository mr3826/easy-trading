"""PostgreSQL persistence for the journal and execution state.

The store is deliberately small and explicit. Every write is parameterized,
order state transitions are transactional, and connection failures propagate
as ``PersistenceUnavailable`` so callers can block new orders.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

import asyncpg


class PersistenceUnavailable(RuntimeError):
    """Raised when durable state cannot be read or written."""


@dataclass(frozen=True)
class RecoveryState:
    open_orders: tuple[dict[str, Any], ...]
    blocked: bool
    reason: str | None


class JournalRepository(Protocol):
    """Durable append-only journal contract."""

    async def append_journal_event(
        self,
        event_id: str,
        event_time: datetime,
        environment: str,
        event_type: str,
        source: str,
        code_version: str,
        config_version: str,
        payload: Mapping[str, Any],
    ) -> None: ...


class OMSRepository(Protocol):
    """Durable OMS contract used by the application boundary."""

    async def create_order_intent(
        self,
        order_id: str,
        idempotency_key: str,
        symbol: str,
        side: str,
        quantity: int,
        created_at: datetime,
    ) -> bool: ...

    async def transition_order(
        self,
        order_id: str,
        status: str,
        broker_order_id: str | None = None,
        updated_at: datetime | None = None,
    ) -> None: ...


class PostgresStore:
    """Async PostgreSQL repository used by OMS and recovery code."""

    def __init__(self, dsn: str, migrations_dir: Path | None = None) -> None:
        self.dsn = dsn
        self.migrations_dir = migrations_dir or Path(__file__).parents[3] / "migrations"
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        try:
            self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=4)
        except Exception as exc:
            raise PersistenceUnavailable("database connection failed") from exc

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    def _require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise PersistenceUnavailable("database is not connected")
        return self.pool

    async def migrate(self) -> None:
        pool = self._require_pool()
        scripts = sorted(self.migrations_dir.glob("*.sql"))
        if not scripts:
            raise PersistenceUnavailable("no database migrations found")
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL)"
                )
                for script in scripts:
                    version = script.name
                    applied = await connection.fetchval("SELECT 1 FROM schema_migrations WHERE version = $1", version)
                    if not applied:
                        await connection.execute(script.read_text(encoding="utf-8"))
                        await connection.execute(
                            "INSERT INTO schema_migrations(version, applied_at) VALUES($1, $2)",
                            version,
                            datetime.now(timezone.utc),
                        )

    async def append_journal_event(
        self,
        event_id: str,
        event_time: datetime,
        environment: str,
        event_type: str,
        source: str,
        code_version: str,
        config_version: str,
        payload: Mapping[str, Any],
    ) -> None:
        pool = self._require_pool()
        event_timestamp = _utc(event_time)
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        checksum_input = json.dumps(
            {
                "event_id": event_id,
                "event_time": event_timestamp.isoformat(),
                "environment": environment,
                "event_type": event_type,
                "source": source,
                "code_version": code_version,
                "config_version": config_version,
                "payload": payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(checksum_input.encode("utf-8")).hexdigest()
        try:
            await pool.execute(
                "INSERT INTO journal_events(event_id,event_time,environment,event_type,source,"
                "code_version,config_version,payload,checksum) VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9)",
                event_id,
                event_timestamp,
                environment,
                event_type,
                source,
                code_version,
                config_version,
                serialized,
                digest,
            )
        except asyncpg.PostgresError as exc:
            raise PersistenceUnavailable("journal write failed") from exc

    async def create_order_intent(
        self,
        order_id: str,
        idempotency_key: str,
        symbol: str,
        side: str,
        quantity: int,
        created_at: datetime,
    ) -> bool:
        """Insert an intent; return false for an existing idempotency key."""
        pool = self._require_pool()
        try:
            result = await pool.execute(
                "INSERT INTO order_intents(order_id,idempotency_key,symbol,side,quantity,created_at) "
                "VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT (idempotency_key) DO NOTHING",
                order_id,
                idempotency_key,
                symbol,
                side,
                quantity,
                _utc(created_at),
            )
        except asyncpg.PostgresError as exc:
            raise PersistenceUnavailable("order intent write failed") from exc
        return result.endswith("1")

    async def record_risk_decision(
        self,
        order_id: str,
        approved: bool,
        reason: str,
        policy_version: str,
        decided_at: datetime,
    ) -> None:
        pool = self._require_pool()
        try:
            await pool.execute(
                "INSERT INTO risk_decisions(order_id,approved,reason,policy_version,decided_at) "
                "VALUES($1,$2,$3,$4,$5) ON CONFLICT (order_id) DO UPDATE SET "
                "approved=EXCLUDED.approved,reason=EXCLUDED.reason,policy_version=EXCLUDED.policy_version,"
                "decided_at=EXCLUDED.decided_at",
                order_id,
                approved,
                reason,
                policy_version,
                _utc(decided_at),
            )
        except asyncpg.PostgresError as exc:
            raise PersistenceUnavailable("risk decision write failed") from exc

    async def record_signal(
        self,
        signal_id: str,
        symbol: str,
        side: str,
        quantity: int,
        decision_time: datetime,
        payload: Mapping[str, Any],
    ) -> None:
        """Persist a strategy signal and its point-in-time payload."""
        pool = self._require_pool()
        try:
            await pool.execute(
                "INSERT INTO signals(signal_id,symbol,side,quantity,decision_time,payload) "
                "VALUES($1,$2,$3,$4,$5,$6::jsonb) ON CONFLICT(signal_id) DO NOTHING",
                signal_id,
                symbol,
                side,
                quantity,
                _utc(decision_time),
                json.dumps(payload, sort_keys=True),
            )
        except asyncpg.PostgresError as exc:
            raise PersistenceUnavailable("signal write failed") from exc

    async def record_policy_version(
        self, policy_version: str, payload: Mapping[str, Any], created_at: datetime
    ) -> None:
        """Persist the exact versioned risk policy used for a decision."""
        pool = self._require_pool()
        try:
            await pool.execute(
                "INSERT INTO policy_versions(policy_version,payload,created_at) VALUES($1,$2::jsonb,$3) "
                "ON CONFLICT(policy_version) DO NOTHING",
                policy_version,
                json.dumps(payload, sort_keys=True),
                _utc(created_at),
            )
        except asyncpg.PostgresError as exc:
            raise PersistenceUnavailable("policy write failed") from exc

    async def record_model_metadata(
        self,
        model_id: str,
        dataset_hash: str,
        code_hash: str,
        model_hash: str,
        payload: Mapping[str, Any],
        created_at: datetime,
    ) -> None:
        """Persist reproducibility hashes for a trained candidate."""
        pool = self._require_pool()
        try:
            await pool.execute(
                "INSERT INTO model_metadata(model_id,dataset_hash,code_hash,model_hash,payload,created_at) "
                "VALUES($1,$2,$3,$4,$5::jsonb,$6) ON CONFLICT(model_id) DO NOTHING",
                model_id,
                dataset_hash,
                code_hash,
                model_hash,
                json.dumps(payload, sort_keys=True),
                _utc(created_at),
            )
        except asyncpg.PostgresError as exc:
            raise PersistenceUnavailable("model metadata write failed") from exc

    async def record_operator_authorization(
        self,
        authorization_id: str,
        account_id: str,
        expires_at: datetime,
        payload: Mapping[str, Any],
        created_at: datetime,
    ) -> None:
        """Persist a time-bounded operator authorization without credentials."""
        pool = self._require_pool()
        try:
            await pool.execute(
                "INSERT INTO operator_authorizations(authorization_id,account_id,expires_at,payload,created_at) "
                "VALUES($1,$2,$3,$4::jsonb,$5)",
                authorization_id,
                account_id,
                _utc(expires_at),
                json.dumps(payload, sort_keys=True),
                _utc(created_at),
            )
        except asyncpg.PostgresError as exc:
            raise PersistenceUnavailable("authorization write failed") from exc

    async def transition_order(
        self,
        order_id: str,
        status: str,
        broker_order_id: str | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        """Persist an OMS transition atomically with its durable order row."""
        pool = self._require_pool()
        try:
            async with pool.acquire() as connection:
                async with connection.transaction():
                    if not await connection.fetchval("SELECT 1 FROM risk_decisions WHERE order_id=$1", order_id):
                        raise PersistenceUnavailable("order has no persisted risk decision")
                    await connection.execute(
                        "INSERT INTO orders(order_id,broker_order_id,status,updated_at) VALUES($1,$2,$3,$4) "
                        "ON CONFLICT(order_id) DO UPDATE SET "
                        "broker_order_id=COALESCE(EXCLUDED.broker_order_id,orders.broker_order_id),"
                        "status=EXCLUDED.status,updated_at=EXCLUDED.updated_at",
                        order_id,
                        broker_order_id,
                        status,
                        _utc(updated_at or datetime.now(timezone.utc)),
                    )
        except PersistenceUnavailable:
            raise
        except asyncpg.PostgresError as exc:
            raise PersistenceUnavailable("order transition failed") from exc

    async def recover(self) -> RecoveryState:
        """Recover open orders; database failure blocks execution."""
        pool = self._require_pool()
        try:
            rows = await pool.fetch(
                "SELECT order_id, broker_order_id, status FROM orders "
                "WHERE status NOT IN ('FILLED','CANCELED','REJECTED','EXPIRED') "
                "ORDER BY updated_at"
            )
        except asyncpg.PostgresError as exc:
            raise PersistenceUnavailable("startup recovery failed") from exc
        return RecoveryState(tuple(dict(row) for row in rows), False, None)

    async def record_fill(
        self,
        execution_id: str,
        order_id: str,
        quantity: int,
        price: float,
        executed_at: datetime,
        broker_trade_id: str | None = None,
    ) -> bool:
        """Persist a fill exactly once using execution and broker IDs."""
        pool = self._require_pool()
        try:
            result = await pool.execute(
                "INSERT INTO fills(execution_id,order_id,broker_trade_id,quantity,price,executed_at) "
                "VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING",
                execution_id,
                order_id,
                broker_trade_id,
                quantity,
                price,
                _utc(executed_at),
            )
        except asyncpg.PostgresError as exc:
            raise PersistenceUnavailable("fill write failed") from exc
        return result.endswith("1")

    async def upsert_position(self, symbol: str, quantity: int, average_cost: float, updated_at: datetime) -> None:
        """Persist the independently reconstructed local position."""
        pool = self._require_pool()
        try:
            await pool.execute(
                "INSERT INTO positions(symbol,quantity,average_cost,updated_at) VALUES($1,$2,$3,$4) "
                "ON CONFLICT(symbol) DO UPDATE SET quantity=EXCLUDED.quantity,"
                "average_cost=EXCLUDED.average_cost,updated_at=EXCLUDED.updated_at",
                symbol,
                quantity,
                average_cost,
                _utc(updated_at),
            )
        except asyncpg.PostgresError as exc:
            raise PersistenceUnavailable("position write failed") from exc

    async def record_reconciliation(
        self,
        reconciliation_id: str,
        checked_at: datetime,
        reconciled: bool,
        blocks_new_orders: bool,
        differences: list[str],
    ) -> None:
        """Persist every reconciliation result, including mismatches."""
        pool = self._require_pool()
        try:
            await pool.execute(
                "INSERT INTO reconciliation_results(reconciliation_id,checked_at,reconciled,"
                "blocks_new_orders,differences) VALUES($1,$2,$3,$4,$5::jsonb)",
                reconciliation_id,
                _utc(checked_at),
                reconciled,
                blocks_new_orders,
                json.dumps(differences),
            )
        except asyncpg.PostgresError as exc:
            raise PersistenceUnavailable("reconciliation write failed") from exc

    async def record_shadow_decision(self, decision: Mapping[str, Any]) -> None:
        """Persist a shadow decision for deterministic replay (contract C3)."""
        decision_id = str(decision.get("decision_id", ""))
        symbol = str(decision.get("symbol", ""))
        action = str(decision.get("action", ""))
        quantity = decision.get("quantity")
        bar_timestamp = decision.get("bar_timestamp")
        if not decision_id or not symbol or not action:
            raise PersistenceUnavailable("shadow decision requires decision_id, symbol, and action")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise PersistenceUnavailable("shadow decision quantity must be a positive integer")
        if not isinstance(bar_timestamp, datetime):
            raise PersistenceUnavailable("shadow decision bar_timestamp must be a timezone-aware datetime")
        try:
            bar_timestamp = _utc(bar_timestamp)
        except ValueError as exc:
            raise PersistenceUnavailable("shadow decision bar_timestamp must be timezone-aware") from exc
        pool = self._require_pool()
        try:
            await pool.execute(
                "INSERT INTO shadow_decisions(decision_id,symbol,action,quantity,bar_timestamp,decision) "
                "VALUES($1,$2,$3,$4,$5,$6::jsonb) ON CONFLICT (decision_id) DO NOTHING",
                decision_id,
                symbol,
                action,
                quantity,
                bar_timestamp,
                json.dumps(decision, sort_keys=True, default=str),
            )
        except asyncpg.PostgresError as exc:
            raise PersistenceUnavailable("shadow decision write failed") from exc

    async def list_shadow_decisions(self) -> list[dict[str, Any]]:
        """List persisted shadow decisions in recorded order."""
        pool = self._require_pool()
        try:
            rows = await pool.fetch(
                "SELECT decision_id, symbol, action, quantity, bar_timestamp, decision, recorded_at "
                "FROM shadow_decisions ORDER BY recorded_at, decision_id"
            )
        except asyncpg.PostgresError as exc:
            raise PersistenceUnavailable("shadow decision read failed") from exc
        return [dict(row) for row in rows]

    async def record_incident(
        self,
        incident_id: str,
        severity: str,
        details: Mapping[str, Any],
        created_at: datetime,
    ) -> None:
        """Persist a safety incident for operator resolution."""
        pool = self._require_pool()
        try:
            await pool.execute(
                "INSERT INTO incidents(incident_id,severity,details,created_at) VALUES($1,$2,$3::jsonb,$4)",
                incident_id,
                severity,
                json.dumps(details, sort_keys=True),
                _utc(created_at),
            )
        except asyncpg.PostgresError as exc:
            raise PersistenceUnavailable("incident write failed") from exc

    async def save_snapshot(
        self,
        snapshot_id: str,
        captured_at: datetime,
        cash: float,
        gross_exposure: float,
        net_exposure: float,
        payload: Mapping[str, Any],
    ) -> None:
        """Persist a portfolio snapshot with UTC capture time."""
        pool = self._require_pool()
        try:
            await pool.execute(
                "INSERT INTO portfolio_snapshots(snapshot_id,captured_at,cash,gross_exposure,"
                "net_exposure,payload) VALUES($1,$2,$3,$4,$5,$6::jsonb)",
                snapshot_id,
                _utc(captured_at),
                cash,
                gross_exposure,
                net_exposure,
                json.dumps(payload, sort_keys=True),
            )
        except asyncpg.PostgresError as exc:
            raise PersistenceUnavailable("snapshot write failed") from exc

    async def fail_closed_recovery(self) -> RecoveryState:
        """Return the safety state to use when recovery cannot complete."""
        try:
            return await self.recover()
        except PersistenceUnavailable as exc:
            return RecoveryState((), True, str(exc))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)
