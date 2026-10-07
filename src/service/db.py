"""
The gateway's Postgres access: a direct connection and the numbered `.sql` migrations under `migrations/`.

`migrate` applies each file not yet recorded in `schema_migrations`, in name order, under an advisory lock so two
gateways starting together apply it once. Connections are direct, never pooled, because `LISTEN/NOTIFY` needs one.
"""
import hashlib
import os

import psycopg

URL_VARIABLE = "DAEDALUS_DATABASE_URL"
MIGRATIONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")


def lock_key(name):
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)


LOCK = lock_key("daedalus.schema_migrations")


def connect(url):
    return psycopg.connect(url, autocommit=True)


def migrate(conn):
    applied_now = []
    conn.execute("SELECT pg_advisory_lock(%s)", (LOCK,))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        for name in sorted(os.listdir(MIGRATIONS)):
            version, extension = os.path.splitext(name)
            if extension != ".sql" or version in done:
                continue
            with open(os.path.join(MIGRATIONS, name), encoding="utf-8") as handle:
                script = handle.read()
            with conn.transaction():
                conn.execute(script)
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            applied_now.append(version)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))
    return applied_now
