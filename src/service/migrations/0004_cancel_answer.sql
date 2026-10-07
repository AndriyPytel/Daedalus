ALTER TABLE runs ADD COLUMN interactive boolean NOT NULL DEFAULT false;
ALTER TABLE runs ADD COLUMN confirm boolean NOT NULL DEFAULT false;

DROP INDEX runs_one_active;
CREATE UNIQUE INDEX runs_one_active ON runs (session_id) WHERE state IN ('queued', 'running', 'needs-answer');
