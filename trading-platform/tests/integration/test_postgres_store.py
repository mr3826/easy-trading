"""Real PostgreSQL integration tests.

Run with ``DATABASE_URL=... uv run pytest -m postgres``. They are skipped in
ordinary local runs when no isolated database has been provisioned.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import pytest
from trading_platform.persistence.postgres import PersistenceUnavailable, PostgresStore

pytestmark = pytest.mark.postgres


def test_postgres_migration_idempotency_and_recovery() -> None:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is required for real PostgreSQL integration")

    async def scenario() -> None:
        store = PostgresStore(
            dsn,
            Path(__file__).parents[2] / "migrations",
        )
        await store.connect()
        try:
            await store.migrate()
            await store.migrate()
            now = datetime.now(timezone.utc)
            run_id = uuid.uuid4().hex
            event_id = f"event-integration-{run_id}"
            order_id = f"order-integration-{run_id}"
            idempotency_key = f"idempotency-integration-{run_id}"
            await store.append_journal_event(
                event_id,
                now,
                "simulation",
                "TEST",
                "integration",
                "test-commit",
                "test-config",
                {"value": 1},
            )
            assert await store.create_order_intent(
                order_id,
                idempotency_key,
                "AAPL",
                "BUY",
                1,
                now,
            )
            assert not await store.create_order_intent(
                f"order-integration-duplicate-{run_id}",
                idempotency_key,
                "AAPL",
                "BUY",
                1,
                now,
            )
            await store.record_risk_decision(order_id, True, "test", "policy-v1", now)
            await store.record_signal(f"signal-{run_id}", "AAPL", "BUY", 1, now, {"close": 100.0})
            await store.record_policy_version(f"policy-{run_id}", {"max_positions": 3}, now)
            await store.record_model_metadata(
                f"model-{run_id}", "dataset", "code", "model", {"fallback": "baseline"}, now
            )
            await store.record_operator_authorization(
                f"authorization-{run_id}",
                "PAPER-TEST",
                now + timedelta(hours=1),
                {"operator": "test"},
                now,
            )
            await store.transition_order(order_id, "OPEN", f"broker-{run_id}", now)
            execution_id = f"execution-integration-{run_id}"
            trade_id = f"trade-{run_id}"
            assert await store.record_fill(execution_id, order_id, 1, 100.0, now, trade_id)
            assert not await store.record_fill(execution_id, order_id, 1, 100.0, now, trade_id)
            await store.upsert_position("AAPL", 1, 100.0, now)
            await store.record_reconciliation(
                f"reconciliation-integration-{run_id}", now, False, True, ["AAPL mismatch"]
            )
            await store.record_incident(f"incident-integration-{run_id}", "CRITICAL", {"reason": "mismatch"}, now)
            await store.save_snapshot(f"snapshot-integration-{run_id}", now, 900.0, 100.0, 100.0, {"AAPL": 1})
            assert store.pool is not None
            async with store.pool.acquire() as connection:
                with pytest.raises(asyncpg.PostgresError):
                    await connection.execute(
                        f"UPDATE journal_events SET event_type='TAMPERED' WHERE event_id='{event_id}'"
                    )
            recovered = await store.recover()
            assert recovered.blocked is False
            assert any(row["order_id"] == order_id for row in recovered.open_orders)
        finally:
            await store.close()
        fail_closed = await store.fail_closed_recovery()
        assert fail_closed.blocked

    asyncio.run(scenario())


def test_shadow_decision_roundtrip_idempotency_and_validation() -> None:
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL is required for real PostgreSQL integration")

    async def scenario() -> None:
        store = PostgresStore(
            dsn,
            Path(__file__).parents[2] / "migrations",
        )
        await store.connect()
        try:
            await store.migrate()
            decision_id = f"shadow-{uuid.uuid4().hex}"
            decision = {
                "decision_id": decision_id,
                "symbol": "AAPL",
                "action": "WOULD_SUBMIT",
                "quantity": 5,
                "bar_timestamp": datetime.now(timezone.utc),
                "confidence": 0.8,
            }
            await store.record_shadow_decision(decision)
            await store.record_shadow_decision(decision)
            rows = await store.list_shadow_decisions()
            row = next(row for row in rows if row["decision_id"] == decision_id)
            assert row["symbol"] == "AAPL"
            assert row["action"] == "WOULD_SUBMIT"
            assert row["quantity"] == 5
            assert json.loads(row["decision"])["confidence"] == 0.8
            assert row["recorded_at"] is not None

            with pytest.raises(PersistenceUnavailable):
                await store.record_shadow_decision(
                    {
                        "symbol": "AAPL",
                        "action": "WOULD_SUBMIT",
                        "quantity": 1,
                        "bar_timestamp": datetime.now(timezone.utc),
                    }
                )
            with pytest.raises(PersistenceUnavailable):
                await store.record_shadow_decision(
                    {
                        "decision_id": f"shadow-{uuid.uuid4().hex}",
                        "symbol": "AAPL",
                        "action": "WOULD_SUBMIT",
                        "quantity": 0,
                        "bar_timestamp": datetime.now(timezone.utc),
                    }
                )
            with pytest.raises(PersistenceUnavailable):
                await store.record_shadow_decision(
                    {
                        "decision_id": f"shadow-{uuid.uuid4().hex}",
                        "symbol": "AAPL",
                        "action": "WOULD_SUBMIT",
                        "quantity": 1,
                        "bar_timestamp": datetime.now(),
                    }
                )
        finally:
            await store.close()

    asyncio.run(scenario())
