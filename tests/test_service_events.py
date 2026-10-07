"""
A client reads a Run's events over SSE, loses the connection, and resumes with `Last-Event-ID` without a gap or a repeat.

The tests run the gateway on a real socket over a fake worker against a real Postgres, skipped as in `test_service_postgres.py`.
The SSE `id` is the event row's `seq`; the one writer of a Run's events is its worker's reader thread, so ids reach a client in order.
"""
import json
import socket
import threading

import httpx
import psycopg
import pytest
import uvicorn

from service import db
from service.app import app
from service.owner import current_owner
from test_service_postgres import _admin_url, conn, database_url, repo  # noqa: F401
from test_service_runs import FINAL, final, start
from test_service_worker_channel import FAKE, until


@pytest.fixture
def server(database_url, monkeypatch):
    monkeypatch.setenv(db.URL_VARIABLE, database_url)
    monkeypatch.setenv("DAEDALUS_WORKER_ARGV", json.dumps(FAKE))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    uv = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=uv.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    until(lambda: uv.started)
    yield "http://127.0.0.1:%d" % sock.getsockname()[1]
    uv.should_exit = True
    thread.join(30)


def post(base, prompt, **body):
    return httpx.post(base + "/v1/runs", json={"prompt": prompt, **body}).json()


def frames(lines):
    frame = {}
    for line in lines:
        if line == "":
            if frame:
                yield frame
            frame = {}
        elif not line.startswith(":"):
            name, _, value = line.partition(": ")
            frame[name] = value
    if frame:
        yield frame


def read(base, run_id, last=None, stop_after=None):
    headers = {"Last-Event-ID": str(last)} if last is not None else {}
    got = []
    with httpx.stream("GET", "%s/v1/runs/%s/events" % (base, run_id), headers=headers, timeout=30) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for frame in frames(response.iter_lines()):
            got.append(frame)
            if stop_after is not None and len(got) == stop_after:
                break
    return got


def stored(database_url, run_id):
    with db.connect(database_url) as connection:
        return [row[0] for row in connection.execute("SELECT seq FROM events WHERE run_id = %s ORDER BY seq", (run_id,))]


def test_streaming_a_finished_run_returns_all_its_events_in_order_then_closes(server, database_url):
    run = post(server, "priced")
    until(lambda: httpx.get("%s/v1/runs/%s" % (server, run["id"])).json()["state"] in FINAL)
    got = read(server, run["id"])
    ids = [int(frame["id"]) for frame in got]
    assert ids == stored(database_url, run["id"]) and len(ids) == 2
    assert [frame["event"] for frame in got] == ["cost", "completed"]
    assert json.loads(got[0]["data"]) == {"usd": 0.25}


def test_a_stream_opened_before_the_run_ends_delivers_live_events_and_closes_at_the_final_state(server, database_url):
    run = post(server, "hello")
    got = read(server, run["id"])
    assert [int(frame["id"]) for frame in got] == stored(database_url, run["id"])
    assert [frame["event"] for frame in got] == ["intent", "completed"]


def test_a_client_that_drops_mid_run_and_resumes_with_last_event_id_gets_exactly_the_missing_events(server, database_url, tmp_path):
    gate = tmp_path / "gate"
    run = post(server, "gated:%s" % gate)
    first = read(server, run["id"], stop_after=5)
    first_ids = [int(frame["id"]) for frame in first]
    assert len(first_ids) == 5
    gate.write_text("go")
    second = read(server, run["id"], last=first_ids[-1])
    second_ids = [int(frame["id"]) for frame in second]
    assert second_ids and min(second_ids) > max(first_ids)
    assert first_ids + second_ids == stored(database_url, run["id"])
    assert len(set(first_ids + second_ids)) == len(first_ids + second_ids) == 11


def test_a_resume_after_the_last_event_returns_nothing_and_closes(server, database_url):
    run = post(server, "hello")
    everything = read(server, run["id"])
    assert read(server, run["id"], last=everything[-1]["id"]) == []


def test_events_written_while_a_stream_replays_arrive_once_and_in_order(server, database_url, repo):
    owner = current_owner()
    repo.ensure_user(owner)
    run = repo.create_run(owner, repo.create_session(owner)["id"])
    repo.update_run(owner, run["id"], state="running")
    for n in range(40):
        repo.append_event(owner, run["id"], "step", {"n": n})
    result = []
    reader = threading.Thread(target=lambda: result.extend(read(server, run["id"])), daemon=True)
    reader.start()
    for n in range(40, 400):
        repo.append_event(owner, run["id"], "step", {"n": n})
    repo.update_run(owner, run["id"], state="completed")
    reader.join(30)
    assert not reader.is_alive()
    assert [int(frame["id"]) for frame in result] == stored(database_url, run["id"])
    assert [json.loads(frame["data"])["n"] for frame in result] == list(range(400))


def test_a_stream_ends_when_the_run_is_failed_by_the_gateway(server, database_url):
    run = post(server, "bye")
    got = read(server, run["id"])
    assert got[-1]["event"] == "failed" and [int(frame["id"]) for frame in got] == stored(database_url, run["id"])


def test_a_closed_client_leaves_no_connection_behind(server, database_url, tmp_path):
    def sessions():
        with db.connect(database_url) as probe:
            return probe.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                                 "AND pid <> pg_backend_pid() AND query LIKE '%%LISTEN%%'").fetchone()[0]

    gate = tmp_path / "gate"
    run = post(server, "gated:%s" % gate)
    until(lambda: len(stored(database_url, run["id"])) == 5)
    clients = [httpx.stream("GET", "%s/v1/runs/%s/events" % (server, run["id"]), timeout=30) for _ in range(5)]
    responses = [client.__enter__() for client in clients]
    for response in responses:
        next(response.iter_lines())
    until(lambda: sessions() == 5)
    for client in clients:
        client.__exit__(None, None, None)
    until(lambda: sessions() == 0)
    gate.write_text("go")
    final_run = until(lambda: (lambda r: r if r["state"] in FINAL else None)(httpx.get("%s/v1/runs/%s" % (server, run["id"])).json()))
    assert final_run["state"] == "completed"
    until(lambda: sessions() == 0)


def test_an_unknown_or_foreign_run_is_not_found_as_problem_json(server, repo):
    mine = post(server, "hello")
    until(lambda: httpx.get("%s/v1/runs/%s" % (server, mine["id"])).json()["state"] in FINAL)
    assert httpx.get("%s/v1/runs/%s/events" % (server, mine["id"])).status_code == 200
    app.dependency_overrides[current_owner] = lambda: repo.create_user()
    try:
        for run_id in ("run_missing", mine["id"]):
            refused = httpx.get("%s/v1/runs/%s/events" % (server, run_id))
            assert refused.status_code == 404 and refused.headers["content-type"] == "application/problem+json"
    finally:
        app.dependency_overrides.clear()


def test_a_last_event_id_that_is_not_a_number_is_a_problem_json(server):
    run = post(server, "hello")
    refused = httpx.get("%s/v1/runs/%s/events" % (server, run["id"]), headers={"Last-Event-ID": "abc"})
    assert refused.status_code == 400 and refused.headers["content-type"] == "application/problem+json"
