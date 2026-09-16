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

CREATE TABLE IF NOT EXISTS signals (
    signal_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    decision_time TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_decisions (
    order_id TEXT PRIMARY KEY REFERENCES order_intents(order_id),
    approved BOOLEAN NOT NULL,
    reason TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    decided_at TIMESTAMPTZ NOT NULL
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

CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    quantity INTEGER NOT NULL,
    average_cost NUMERIC NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    captured_at TIMESTAMPTZ NOT NULL,
    cash NUMERIC NOT NULL,
    gross_exposure NUMERIC NOT NULL,
    net_exposure NUMERIC NOT NULL,
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS reconciliation_results (
    reconciliation_id TEXT PRIMARY KEY,
    checked_at TIMESTAMPTZ NOT NULL,
    reconciled BOOLEAN NOT NULL,
    blocks_new_orders BOOLEAN NOT NULL,
    differences JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_versions (
    policy_version TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS model_metadata (
    model_id TEXT PRIMARY KEY,
    dataset_hash TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    model_hash TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS operator_authorizations (
    authorization_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE OR REPLACE FUNCTION prevent_journal_update() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'journal_events is append-only'; END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS journal_events_immutable ON journal_events;
CREATE TRIGGER journal_events_immutable BEFORE UPDATE OR DELETE ON journal_events
FOR EACH ROW EXECUTE FUNCTION prevent_journal_update();
