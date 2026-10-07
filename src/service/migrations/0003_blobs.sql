CREATE TABLE blobs (
    id text PRIMARY KEY,
    owner_id text NOT NULL REFERENCES users (id),
    key text NOT NULL UNIQUE,
    sha256 text NOT NULL,
    size bigint NOT NULL,
    kind text NOT NULL CHECK (kind IN ('attachment', 'step', 'checkpoint')),
    name text NOT NULL,
    session_id text REFERENCES sessions (id),
    run_id text REFERENCES runs (id),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX blobs_one_result_per_run ON blobs (run_id, kind) WHERE run_id IS NOT NULL;
CREATE INDEX blobs_owner ON blobs (owner_id, created_at);
