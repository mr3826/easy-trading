-- Shadow decisions archive for deterministic replay (contract C3).
CREATE TABLE IF NOT EXISTS shadow_decisions (
    decision_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    bar_timestamp TIMESTAMPTZ NOT NULL,
    decision JSONB NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
