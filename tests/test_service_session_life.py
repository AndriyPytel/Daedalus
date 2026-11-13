"""
A Session outlives its worker: only `max_sessions` workers run at once, an idle worker is stopped after the idle TTL while its Session stays listed, and the next Run respawns it from the last checkpoint and the earlier conversation.

The tests run the app over a fake worker against a real Postgres, skipped as in `test_service_postgres.py`.
The respawn tests with the scripted worker on the real kernel skip when FreeCAD cannot be imported.
"""
import hashlib
import json
import os
import sys
import time

import pytest
from fastapi.testclient import TestClient

from service import db
from service.app import app
from service.blobs_api import ROOT_VARIABLE
from service.blobstore import LocalBlobStore
from service.repository import Repository
from test_service_postgres import _admin_url, conn, database_url, repo  # noqa: F401
from test_service_runs import AUTH, FINAL, TOKEN, final, start
from test_service_worker_channel import FAKE, alive, until

TTL = "1.5"


@pytest.fixture
def api(database_url, monkeypatch, tmp_path):
    monkeypatch.setenv(ROOT_VARIABLE, str(tmp_path / "blobs"))
    monkeypatch.setenv(db.URL_VARIABLE, database_url)
    monkeypatch.setenv("DAEDALUS_WORKER_ARGV", json.dumps(FAKE))
    monkeypatch.setenv("DAEDALUS_TOKEN", TOKEN)
    monkeypatch.setenv("DAEDALUS_MAX_SESSIONS", "2")
    monkeypatch.setenv("DAEDALUS_IDLE_TTL", TTL)
    with TestClient(app, headers=AUTH) as client:
        yield client


def session_state(api, session_id):
    return api.get("/v1/sessions/%s" % session_id).json()["state"]


def event_data(database_url, run_id, kind):
    with db.connect(database_url) as check:
        rows = check.execute("SELECT payload FROM events WHERE run_id = %s AND type = %s ORDER BY seq", (run_id, kind)).fetchall()
    return [row[0] for row in rows]


def test_a_third_session_beyond_max_sessions_is_queued_and_starts_when_a_slot_frees(api):
    first, second = (start(api, "pid").json() for _ in range(2))
    final(api, first["id"]), final(api, second["id"])
    third = start(api, "pid").json()
    until(lambda: session_state(api, third["session_id"]) == "queued")
    assert api.get("/v1/runs/%s" % third["id"]).json()["state"] == "queued"
    assert final(api, third["id"])["state"] == "completed"
    assert session_state(api, third["session_id"]) == "active"


def test_a_queued_run_that_is_cancelled_never_starts_a_worker(api):
    first, second = (start(api, "pid").json() for _ in range(2))
    final(api, first["id"]), final(api, second["id"])
    third = start(api, "pid").json()
    assert api.post("/v1/runs/%s:cancel" % third["id"]).status_code == 202
    until(lambda: session_state(api, third["session_id"]) == "idle")
    assert api.get("/v1/runs/%s" % third["id"]).json()["state"] == "cancelled"


def test_after_the_idle_ttl_the_worker_process_is_gone_and_the_session_is_still_listed(api, database_url):
    run = start(api, "pid").json()
    final(api, run["id"])
    pid = event_data(database_url, run["id"], "pid")[0]["pid"]
    assert alive(pid)
    until(lambda: not alive(pid))
    until(lambda: session_state(api, run["session_id"]) == "idle")
    assert run["session_id"] in [item["id"] for item in api.get("/v1/sessions").json()["data"]]
    assert api.get("/v1/sessions/%s" % run["session_id"]).json()["closed_at"] is None


def test_a_busy_worker_is_not_stopped_by_the_idle_ttl(api, tmp_path):
    gate = tmp_path / "go"
    run = start(api, "gated:%s" % gate).json()
    until(lambda: event_data(os.environ[db.URL_VARIABLE], run["id"], "step"), seconds=10)
    time.sleep(float(TTL) * 2.5)
    assert api.get("/v1/runs/%s" % run["id"]).json()["state"] == "running"
    gate.write_text("go")
    assert final(api, run["id"])["state"] == "completed"


def test_a_run_in_an_idle_session_respawns_a_worker_with_the_last_checkpoint_and_the_earlier_conversation(api, database_url):
    first = start(api, "routed").json()
    final(api, first["id"])
    until(lambda: session_state(api, first["session_id"]) == "idle")
    second = start(api, "restored", session_id=first["session_id"]).json()
    assert final(api, second["id"])["state"] == "completed"
    seen = event_data(database_url, second["id"], "restored")[0]
    assert seen["sha256"] == hashlib.sha256(b"PK\x03\x04first").hexdigest()
    assert seen["history"] == [{"role": "user", "content": "routed"}, {"role": "assistant", "content": "design"}]


def test_a_worker_that_is_still_alive_is_not_handed_a_restore(api, database_url):
    first = start(api, "routed").json()
    final(api, first["id"])
    second = start(api, "restored", session_id=first["session_id"]).json()
    final(api, second["id"])
    assert event_data(database_url, second["id"], "restored")[0]["restore"] is False


def test_the_conversation_holds_only_runs_that_were_routed_and_none_after_the_run_asked_about(repo):
    owner = repo.create_user()
    session = repo.create_session(owner)
    routed = repo.create_run(owner, session["id"], "build a box")
    repo.append_event(owner, routed["id"], "routed", {"route": "design"})
    repo.update_run(owner, routed["id"], state="completed")
    cut = repo.create_run(owner, session["id"], "never routed")
    repo.update_run(owner, cut["id"], state="failed")
    current = repo.create_run(owner, session["id"], "make it taller")
    repo.append_event(owner, current["id"], "routed", {"route": "design"})
    assert repo.conversation(owner, session["id"], current["id"]) == [
        {"role": "user", "content": "build a box"}, {"role": "assistant", "content": "design"}]


def test_a_gateway_start_fails_the_runs_no_worker_is_left_to_finish_and_frees_their_sessions(database_url, monkeypatch):
    with db.connect(database_url) as setup:
        db.migrate(setup)
        repo = Repository(setup)
        owner = "usr_owner"
        repo.ensure_user(owner)
        stuck = []
        for state in ("queued", "running", "needs-answer"):
            session = repo.create_session(owner)
            stuck.append((session["id"], repo.update_run(owner, repo.create_run(owner, session["id"])["id"], state=state)["id"]))
        done = repo.create_run(owner, repo.create_session(owner)["id"])
        repo.update_run(owner, done["id"], state="completed")
    monkeypatch.setenv(db.URL_VARIABLE, database_url)
    monkeypatch.setenv("DAEDALUS_WORKER_ARGV", json.dumps(FAKE))
    monkeypatch.setenv("DAEDALUS_TOKEN", TOKEN)
    with TestClient(app, headers=AUTH) as api:
        for session_id, run_id in stuck:
            run = api.get("/v1/runs/%s" % run_id).json()
            assert run["state"] == "failed"
            assert "restart" in event_data(database_url, run_id, "error")[0]["message"]
            assert start(api, "hello", session_id=session_id).status_code == 202
        assert api.get("/v1/runs/%s" % done["id"]).json()["state"] == "completed"
        assert event_data(database_url, done["id"], "error") == []


def test_closing_a_session_leaves_the_pool_holding_nothing_of_it(api):
    run = start(api, "hello").json()
    final(api, run["id"])
    assert api.delete("/v1/sessions/%s" % run["session_id"]).status_code == 200
    pool = app.state.pool
    assert run["session_id"] not in pool._workers and run["session_id"] not in pool._locks
    assert start(api, "hello", session_id=run["session_id"]).status_code == 409


def test_a_session_closed_while_it_waits_for_a_slot_fails_its_run(api):
    first, second = (start(api, "pid").json() for _ in range(2))
    final(api, first["id"]), final(api, second["id"])
    third = start(api, "pid").json()
    assert api.delete("/v1/sessions/%s" % third["session_id"]).status_code == 200
    run = final(api, third["id"])
    assert run["state"] == "failed"


def real_kernel():
    try:
        import FreeCAD  # noqa: F401
    except ImportError:
        pytest.skip("FreeCAD is not importable")


def volumes_of(data, tmp_path):
    import FreeCAD
    path = tmp_path / "model.FCStd"
    path.write_bytes(data)
    document = FreeCAD.openDocument(str(path))
    try:
        return {obj.Name: (obj.Shape.Volume if hasattr(obj, "Shape") else None) for obj in document.Objects}
    finally:
        FreeCAD.closeDocument(document.Name)


HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTED = [sys.executable, os.path.join(os.path.dirname(HERE), "deploy", "worker", "scripted_worker.py")]


def respawn_through(argv, database_url, monkeypatch, tmp_path):
    monkeypatch.setenv(db.URL_VARIABLE, database_url)
    monkeypatch.setenv("DAEDALUS_WORKER_ARGV", json.dumps(argv))
    monkeypatch.setenv("DAEDALUS_TOKEN", TOKEN)
    monkeypatch.setenv(ROOT_VARIABLE, str(tmp_path / "blobs"))
    monkeypatch.setenv("DAEDALUS_IDLE_TTL", "3")
    with TestClient(app, headers=AUTH) as api:
        first = start(api, "Add a 10mm cube").json()
        done = until(lambda: (lambda r: r if r["state"] in FINAL else None)(api.get("/v1/runs/%s" % first["id"]).json()), seconds=420)
        assert done["state"] == "completed", event_data(database_url, first["id"], "error")
        until(lambda: session_state(api, first["session_id"]) == "idle", seconds=60)
        second = start(api, "Make the box 20mm long", session_id=first["session_id"]).json()
        done = until(lambda: (lambda r: r if r["state"] in FINAL else None)(api.get("/v1/runs/%s" % second["id"]).json()), seconds=420)
        assert done["state"] == "completed", event_data(database_url, second["id"], "error")
        with db.connect(database_url) as check:
            repo = Repository(check)
            owner = check.execute("SELECT owner_id FROM runs WHERE id = %s", (first["id"],)).fetchone()[0]
            blobs = [LocalBlobStore(str(tmp_path / "blobs")).get(repo.get_run_blob(owner, run["id"], "checkpoint")["key"])
                     for run in (first, second)]
    return first, second, blobs


def assert_edited_with_history(first, second, before, after, tmp_path, calls):
    assert volumes_of(before, tmp_path)["Body"] == pytest.approx(1000.0)
    assert volumes_of(after, tmp_path)["Body"] == pytest.approx(2000.0)
    workers = list(dict.fromkeys(call["worker"] for call in calls))
    assert len(workers) == 2
    sent = [call for call in calls if call["worker"] == workers[1]][0]["messages"]
    assert sent == [{"role": "user", "content": "Add a 10mm cube"}, {"role": "assistant", "content": "design"},
                    {"role": "user", "content": "Make the box 20mm long"}]


def test_a_second_run_after_the_ttl_edits_the_model_and_still_holds_the_first_runs_geometry(database_url, monkeypatch, tmp_path):
    real_kernel()
    monkeypatch.setenv("DAE_SCRIPT", "cube")
    monkeypatch.setenv("DAE_CALLS", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(p for p in sys.path if p))
    first, second, (before, after) = respawn_through(SCRIPTED, database_url, monkeypatch, tmp_path)
    calls = [json.loads(line) for line in open(tmp_path / "calls.jsonl")]
    assert_edited_with_history(first, second, before, after, tmp_path, calls)


IMAGE = "dae-504-worker"
REMOTE = "dae-504-build"


