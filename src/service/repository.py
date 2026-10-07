"""
Every read and write of users, preferences, Sessions, Runs, events and blob rows, each scoped by `owner_id`.

A row of another owner is as absent as a missing one: reads return nothing and writes raise `LookupError`. Events have
no update or delete here, and the database refuses them too; an append notifies channel `events` with the row id only.
"""
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from service.ids import new_id


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

    def list_sessions(self, owner_id):
        return self._all("SELECT * FROM sessions WHERE owner_id = %s ORDER BY id", (owner_id,))

    def create_run(self, owner_id, session_id):
        if self.get_session(owner_id, session_id) is None:
            raise LookupError(session_id)
        return self._one("INSERT INTO runs (id, owner_id, session_id) VALUES (%s, %s, %s) RETURNING *",
                         (new_id("run"), owner_id, session_id))

    def get_run(self, owner_id, run_id):
        return self._one("SELECT * FROM runs WHERE owner_id = %s AND id = %s", (owner_id, run_id))

    def list_runs(self, owner_id, session_id):
        return self._all("SELECT * FROM runs WHERE owner_id = %s AND session_id = %s ORDER BY id", (owner_id, session_id))

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

    def create_blob(self, owner_id, key, sha256, size, kind, session_id=None, run_id=None, name=None, blob_id=None):
        return self._one(
            "INSERT INTO blobs (id, owner_id, key, sha256, size, kind, name, session_id, run_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (blob_id or new_id("blob"), owner_id, key, sha256, size, kind, name or key.rsplit("/", 1)[-1], session_id, run_id))

    def get_blob(self, owner_id, blob_id):
        return self._one("SELECT * FROM blobs WHERE owner_id = %s AND id = %s", (owner_id, blob_id))

    def get_run_blob(self, owner_id, run_id, kind):
        return self._one("SELECT * FROM blobs WHERE owner_id = %s AND run_id = %s AND kind = %s", (owner_id, run_id, kind))

    def count_session_blobs(self, owner_id, session_id, kind):
        return self._one("SELECT count(*) AS n FROM blobs WHERE owner_id = %s AND session_id = %s AND kind = %s",
                         (owner_id, session_id, kind))["n"]
