CREATE TABLE users (
    id text PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE preferences (
    owner_id text NOT NULL REFERENCES users (id),
    key text NOT NULL,
    value jsonb NOT NULL,
    PRIMARY KEY (owner_id, key)
);

CREATE TABLE sessions (
    id text PRIMARY KEY,
    owner_id text NOT NULL REFERENCES users (id),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sessions_owner ON sessions (owner_id, created_at);

CREATE TABLE runs (
    id text PRIMARY KEY,
    owner_id text NOT NULL REFERENCES users (id),
    session_id text NOT NULL REFERENCES sessions (id),
    state text NOT NULL DEFAULT 'queued',
    cost_usd numeric(12, 4) NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX runs_session ON runs (owner_id, session_id, created_at);

CREATE TABLE events (
    seq bigserial PRIMARY KEY,
    owner_id text NOT NULL REFERENCES users (id),
    run_id text NOT NULL REFERENCES runs (id),
    type text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX events_run ON events (run_id, seq);

CREATE FUNCTION events_are_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'events are append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER events_no_change BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION events_are_append_only();
