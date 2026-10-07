"""
Every read and write of users, preferences, Sessions, Runs and events, each scoped by `owner_id`.

A row of another owner is as absent as a missing one: reads return nothing and writes raise `LookupError`, events have no update or delete, and an append notifies channel `events` with the row id only.
`start_run` makes the Session and the Run in one transaction and returns the Run an `Idempotency-Key` already made; lists page by id, which is creation order.
"""
import psycopg.errors
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from service.ids import new_id


class SessionClosed(Exception):
    pass


class RunActive(Exception):
    pass


class Repository:
    def __init__(self, conn):
        self._conn = conn

    def _all(self, sql, params=()):
        with self._conn.cursor(row_factory=dict_row) as cursor:
            return cursor.execute(sql, params).fetchall()

    def _one(self, sql, params=()):
        rows = self._all(sql, params)
        return rows[0] if rows else None

    def create_user(self):
        user_id = new_id("usr")
        self._conn.execute("INSERT INTO users (id) VALUES (%s)", (user_id,))
        return user_id

    def set_preference(self, owner_id, key, value):
        self._conn.execute(
            "INSERT INTO preferences (owner_id, key, value) VALUES (%s, %s, %s) "
            "ON CONFLICT (owner_id, key) DO UPDATE SET value = EXCLUDED.value",
            (owner_id, key, Jsonb(value)))

    def list_preferences(self, owner_id):
        rows = self._all("SELECT key, value FROM preferences WHERE owner_id = %s", (owner_id,))
        return {row["key"]: row["value"] for row in rows}

    def create_session(self, owner_id):
        return self._one("INSERT INTO sessions (id, owner_id) VALUES (%s, %s) RETURNING *", (new_id("ses"), owner_id))

    def get_session(self, owner_id, session_id):
        return self._one("SELECT * FROM sessions WHERE owner_id = %s AND id = %s", (owner_id, session_id))

    def list_sessions(self, owner_id, after="", limit=None):
        return self._all("SELECT * FROM sessions WHERE owner_id = %s AND id > %s ORDER BY id LIMIT %s",
                         (owner_id, after, limit))

    def close_session(self, owner_id, session_id):
        row = self._one("UPDATE sessions SET closed_at = coalesce(closed_at, now()) WHERE owner_id = %s AND id = %s "
                        "RETURNING *", (owner_id, session_id))
        if row is None:
            raise LookupError(session_id)
        return row

    def ensure_user(self, user_id):
        self._conn.execute("INSERT INTO users (id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))

    def create_run(self, owner_id, session_id, prompt="", attachments=(), key=None):
        session = self.get_session(owner_id, session_id)
        if session is None:
            raise LookupError(session_id)
        if session["closed_at"] is not None:
            raise SessionClosed(session_id)
        try:
            return self._one(
                "INSERT INTO runs (id, owner_id, session_id, prompt, attachments, idempotency_key) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
                (new_id("run"), owner_id, session_id, prompt, Jsonb(list(attachments)), key))
        except psycopg.errors.UniqueViolation as error:
            if error.diag.constraint_name == "runs_one_active":
                raise RunActive(session_id) from error
            raise

    def start_run(self, owner_id, prompt, attachments=(), session_id=None, key=None):
        self.ensure_user(owner_id)
        if key is not None:
            replay = self.get_run_by_key(owner_id, key)
            if replay is not None:
                return replay, False
        try:
            with self._conn.transaction():
                if session_id is None:
                    session_id = self.create_session(owner_id)["id"]
                return self.create_run(owner_id, session_id, prompt, attachments, key), True
        except psycopg.errors.UniqueViolation:
            return self.get_run_by_key(owner_id, key), False

    def get_run_by_key(self, owner_id, key):
        return self._one("SELECT * FROM runs WHERE owner_id = %s AND idempotency_key = %s", (owner_id, key))

    def fail_run(self, owner_id, run_id, message):
        self.append_event(owner_id, run_id, "error", {"message": message})
        self._conn.execute("UPDATE runs SET state = 'failed' WHERE owner_id = %s AND id = %s "
                           "AND state IN ('queued', 'running')", (owner_id, run_id))

    def add_run_cost(self, owner_id, run_id, usd):
        self._conn.execute("UPDATE runs SET cost_usd = cost_usd + %s WHERE owner_id = %s AND id = %s",
                           (usd, owner_id, run_id))

    def get_run(self, owner_id, run_id):
        return self._one("SELECT * FROM runs WHERE owner_id = %s AND id = %s", (owner_id, run_id))

    def list_runs(self, owner_id, session_id=None, after="", limit=None):
        return self._all("SELECT * FROM runs WHERE owner_id = %s AND (%s::text IS NULL OR session_id = %s) "
                         "AND id > %s ORDER BY id LIMIT %s", (owner_id, session_id, session_id, after, limit))

    def update_run(self, owner_id, run_id, state=None, cost_usd=None):
        row = self._one(
            "UPDATE runs SET state = coalesce(%s, state), cost_usd = coalesce(%s, cost_usd) "
            "WHERE owner_id = %s AND id = %s RETURNING *", (state, cost_usd, owner_id, run_id))
        if row is None:
            raise LookupError(run_id)
        return row

    def append_event(self, owner_id, run_id, type, payload):
        if self.get_run(owner_id, run_id) is None:
            raise LookupError(run_id)
        with self._conn.transaction():
            seq = self._one("INSERT INTO events (owner_id, run_id, type, payload) VALUES (%s, %s, %s, %s) RETURNING seq",
                            (owner_id, run_id, type, Jsonb(payload)))["seq"]
            self._conn.execute("SELECT pg_notify('events', %s)", (str(seq),))
        return seq

    def list_events(self, owner_id, run_id, after=0):
        return self._all("SELECT * FROM events WHERE owner_id = %s AND run_id = %s AND seq > %s ORDER BY seq",
                         (owner_id, run_id, after))
