ALTER TABLE sessions ADD COLUMN state text NOT NULL DEFAULT 'idle' CHECK (state IN ('idle', 'queued', 'active'));
