-- Core append-only journal and idempotent execution records.
CREATE TABLE IF NOT EXISTS journal_events (
    event_id TEXT PRIMARY KEY,
    event_time TIMESTAMPTZ NOT NULL,
    ingestion_time TIMESTAMPTZ NOT NULL DEFAULT now(),
    environment TEXT NOT NULL CHECK (environment IN ('research','simulation','shadow','paper','live')),
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    code_version TEXT NOT NULL,
    config_version TEXT NOT NULL,
    payload JSONB NOT NULL,
    checksum TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS order_intents (
    order_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY REFERENCES order_intents(order_id),
    broker_order_id TEXT UNIQUE,
    status TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
    execution_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    broker_trade_id TEXT UNIQUE,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    price NUMERIC NOT NULL CHECK (price > 0),
    executed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    severity TEXT NOT NULL,
    resolved_at TIMESTAMPTZ,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE OR REPLACE FUNCTION prevent_journal_update() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'journal_events is append-only'; END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS journal_events_immutable ON journal_events;
CREATE TRIGGER journal_events_immutable BEFORE UPDATE OR DELETE ON journal_events
FOR EACH ROW EXECUTE FUNCTION prevent_journal_update();
