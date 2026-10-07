ALTER TABLE sessions ADD COLUMN closed_at timestamptz;

ALTER TABLE runs ADD COLUMN prompt text NOT NULL DEFAULT '';
ALTER TABLE runs ADD COLUMN attachments jsonb NOT NULL DEFAULT '[]';
ALTER TABLE runs ADD COLUMN idempotency_key text;

CREATE UNIQUE INDEX runs_idempotency ON runs (owner_id, idempotency_key);
CREATE UNIQUE INDEX runs_one_active ON runs (session_id) WHERE state IN ('queued', 'running');
