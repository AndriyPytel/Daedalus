"""
A Run's events as Server-Sent Events, resumable by `Last-Event-ID`.

Each stream holds its own direct connection: it listens first, then replays rows after the given `seq` and reads again at every wake-up, so a row written during the replay is read once and none is skipped.
Per Run the events are appended serially by one worker reader, so `seq` order is commit order; the stream ends when the Run is final and no row is left, and the connection closes with it.
"""
import json

import psycopg
from psycopg.rows import dict_row

from service.supervisor import TERMINAL

HEARTBEAT = 15.0
PAGE = 500


def frame(row):
    return "id: %d\nevent: %s\ndata: %s\n\n" % (row["seq"], row["type"], json.dumps(row["payload"], separators=(",", ":")))


async def stream(url, owner_id, run_id, after, heartbeat=HEARTBEAT):
    async with await psycopg.AsyncConnection.connect(url, autocommit=True, row_factory=dict_row) as conn:
        await conn.execute("LISTEN events")
        await conn.execute("LISTEN runs")
        while True:
            state = await (await conn.execute("SELECT state FROM runs WHERE owner_id = %s AND id = %s", (owner_id, run_id))).fetchone()
            rows = await (await conn.execute(
                "SELECT seq, type, payload FROM events WHERE owner_id = %s AND run_id = %s AND seq > %s ORDER BY seq LIMIT %s",
                (owner_id, run_id, after, PAGE))).fetchall()
            for row in rows:
                after = row["seq"]
                yield frame(row)
            if rows:
                continue
            if state is None or state["state"] in TERMINAL:
                return
            woke = False
            async for _ in conn.notifies(timeout=heartbeat, stop_after=1):
                woke = True
            if not woke:
                yield ": keep-alive\n\n"
