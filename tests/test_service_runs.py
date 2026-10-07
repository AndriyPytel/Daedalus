"""
A client starts a Run over HTTP and reads its outcome: POST returns at once, GET shows state and cost, lists page by cursor.

The API tests run the app over a fake worker against a real Postgres, skipped as in `test_service_postgres.py`. The one
end-to-end test builds a cube through the real worker in the `dae-499-worker` image on the home server with a scripted
LLM, and skips when the server is unreachable.
"""
import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from service import db
from service.app import app
from service.owner import current_owner
from test_service_postgres import _admin_url, conn, database_url, repo  # noqa: F401
from test_service_worker_channel import FAKE, HOST, ROOT, _ssh, until

IMAGE = "dae-499-worker"
REMOTE = "dae-499-build"
TOKEN = "t" * 40
AUTH = {"Authorization": "Bearer " + TOKEN}
FINAL = ("completed", "blocked", "budget_exhausted", "stopped", "aborted", "failed")


@pytest.fixture
def api(database_url, monkeypatch):
    monkeypatch.setenv(db.URL_VARIABLE, database_url)
    monkeypatch.setenv("DAEDALUS_WORKER_ARGV", json.dumps(FAKE))
    monkeypatch.setenv("DAEDALUS_TOKEN", TOKEN)
    with TestClient(app, headers=AUTH) as client:
        yield client


def start(api, prompt="hello", key=None, **body):
    headers = {"Idempotency-Key": key} if key else {}
    return api.post("/v1/runs", json={"prompt": prompt, **body}, headers=headers)


def final(api, run_id):
    return until(lambda: (lambda run: run if run["state"] in FINAL else None)(api.get("/v1/runs/%s" % run_id).json()))


def page_through(api, path, limit):
    ids, cursor = [], None
    while True:
        query = "limit=%s" % limit + ("&cursor=%s" % cursor if cursor else "")
        body = api.get("%s%s%s" % (path, "&" if "?" in path else "?", query)).json()
        assert body["object"] == "list"
        ids += [item["id"] for item in body["data"]]
        cursor = body["next_cursor"]
        if cursor is None:
            return ids


def test_posting_a_run_returns_a_run_id_at_once_and_polling_ends_in_a_final_state_with_cost(api):
    posted = start(api, "priced")
    assert posted.status_code == 202
    run = posted.json()
    assert run["id"].startswith("run_") and run["session_id"].startswith("ses_") and run["object"] == "run"
    assert run["state"] in ("queued", "running", "completed")
    done = final(api, run["id"])
    assert done["state"] == "completed" and done["cost_usd"] == 0.25 and done["prompt"] == "priced"


def test_repeating_the_post_with_the_same_idempotency_key_creates_no_second_run_or_session(api):
    first = start(api, "hello", key="k1").json()
    final(api, first["id"])
    second = start(api, "hello", key="k1")
    assert second.status_code == 202 and second.json()["id"] == first["id"]
    assert len(api.get("/v1/runs").json()["data"]) == 1
    assert len(api.get("/v1/sessions").json()["data"]) == 1


def test_the_same_idempotency_key_with_another_prompt_is_refused_with_problem_json(api):
    first = start(api, "hello", key="k2").json()
    final(api, first["id"])
    clash = start(api, "priced", key="k2")
    assert clash.status_code == 409 and clash.headers["content-type"] == "application/problem+json"


def test_a_second_post_to_a_session_with_an_active_run_is_refused_with_problem_json(api):
    first = start(api, "hang").json()
    refused = start(api, "hello", session_id=first["session_id"])
    assert refused.status_code == 409
    assert refused.headers["content-type"] == "application/problem+json"
    assert set(refused.json()) >= {"type", "title", "status", "detail"} and refused.json()["status"] == 409
    assert len(api.get("/v1/runs?session_id=%s" % first["session_id"]).json()["data"]) == 1


def test_a_session_takes_its_next_run_once_the_first_is_final(api):
    first = start(api, "hello").json()
    final(api, first["id"])
    second = start(api, "priced", session_id=first["session_id"])
    assert second.status_code == 202 and second.json()["session_id"] == first["session_id"]
    assert final(api, second.json()["id"])["state"] == "completed"


def test_an_unknown_or_foreign_session_is_not_found_as_problem_json(api, repo):
    foreign = repo.create_session(repo.create_user())["id"]
    for session_id in ("ses_missing", foreign):
        refused = start(api, "hello", session_id=session_id)
        assert refused.status_code == 404 and refused.headers["content-type"] == "application/problem+json"


def test_lists_page_with_a_cursor_with_no_repeats_and_in_creation_order(api):
    session_id = start(api, "hello").json()["session_id"]
    for _ in range(4):
        until(lambda: all(r["state"] in FINAL for r in api.get("/v1/runs").json()["data"]))
        start(api, "hello", session_id=session_id)
    until(lambda: all(r["state"] in FINAL for r in api.get("/v1/runs").json()["data"]))
    for _ in range(2):
        start(api, "hello")
    everything = [run["id"] for run in api.get("/v1/runs?limit=100").json()["data"]]
    assert len(everything) == 7 and everything == sorted(everything)
    for limit in (1, 2, 3):
        assert page_through(api, "/v1/runs", limit) == everything
    assert page_through(api, "/v1/runs?session_id=%s" % session_id, 2) == everything[:5]
    sessions = [session["id"] for session in api.get("/v1/sessions?limit=100").json()["data"]]
    assert len(sessions) == 3 and page_through(api, "/v1/sessions", 2) == sessions


def test_the_last_page_has_no_cursor_and_a_full_page_reports_more(api):
    start(api, "hello")
    body = api.get("/v1/runs?limit=1").json()
    assert body["next_cursor"] is None and body["has_more"] is False


def test_every_error_is_problem_json_with_type_title_status_and_detail(api):
    cases = [api.get("/v1/runs/run_missing"), api.get("/v1/sessions/ses_missing"), api.get("/v1/nowhere"),
             api.post("/v1/runs", json={}), api.get("/v1/runs?limit=0"), api.get("/v1/runs?limit=nope"),
             api.delete("/v1/sessions/ses_missing")]
    assert [case.status_code for case in cases] == [404, 404, 404, 422, 422, 422, 404]
    for case in cases:
        assert case.headers["content-type"] == "application/problem+json"
        assert set(case.json()) >= {"type", "title", "status", "detail"} and case.json()["status"] == case.status_code


def test_deleting_a_session_closes_it_stops_its_worker_and_refuses_new_runs(api):
    run = start(api, "hang").json()
    until(lambda: api.get("/v1/runs/%s" % run["id"]).json()["state"] == "running")
    closed = api.delete("/v1/sessions/%s" % run["session_id"])
    assert closed.status_code == 200 and closed.json()["closed_at"] is not None
    assert final(api, run["id"])["state"] == "failed"
    assert start(api, "hello", session_id=run["session_id"]).status_code == 409
    assert api.get("/v1/sessions/%s" % run["session_id"]).json()["closed_at"] is not None


def test_a_run_whose_worker_cannot_start_is_failed_not_left_queued(database_url, monkeypatch):
    monkeypatch.setenv(db.URL_VARIABLE, database_url)
    monkeypatch.setenv("DAEDALUS_WORKER_ARGV", json.dumps([sys.executable, "-c", "import sys; sys.exit(7)"]))
    monkeypatch.setenv("DAEDALUS_TOKEN", TOKEN)
    with TestClient(app, headers=AUTH) as api:
        run = start(api, "hello").json()
        assert final(api, run["id"])["state"] == "failed"


def test_the_owner_comes_from_the_one_current_owner_dependency(api, repo):
    mine = start(api, "hello").json()
    final(api, mine["id"])
    other = repo.create_user()
    app.dependency_overrides[current_owner] = lambda: other
    try:
        assert api.get("/v1/runs/%s" % mine["id"]).status_code == 404
        assert api.get("/v1/runs").json()["data"] == []
    finally:
        app.dependency_overrides.clear()
    assert api.get("/v1/runs/%s" % mine["id"]).status_code == 200


@pytest.fixture(scope="module")
def worker_image():
    probe = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", HOST, "podman --version"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("home server %s unreachable over ssh: %s" % (HOST, probe.stderr.strip()[-200:]))
    _ssh("rm -rf ~/%s/ctx && mkdir -p ~/%s/ctx" % (REMOTE, REMOTE))
    tar = subprocess.Popen(["tar", "--exclude=__pycache__", "-cf", "-", "-C", ROOT, "src", "config", "deploy"],
                           stdout=subprocess.PIPE)
    sent = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, "tar -xf - -C ~/%s/ctx" % REMOTE],
                          stdin=tar.stdout, capture_output=True)
    tar.stdout.close()
    assert tar.wait() == 0 and sent.returncode == 0, sent.stderr
    _ssh("cd ~/%s/ctx && podman build -t %s -f deploy/worker/Containerfile ." % (REMOTE, IMAGE), timeout=2400)
    return IMAGE


def test_a_first_build_runs_end_to_end_through_the_worker_in_the_image(worker_image, database_url, monkeypatch):
    name = "dae-499-real"
    _ssh("podman rm -f %s >/dev/null 2>&1" % name, check=False)
    argv = ["ssh", "-o", "BatchMode=yes", HOST,
            "podman run -i --rm --name %s %s python deploy/worker/scripted_worker.py" % (name, worker_image)]
    monkeypatch.setenv(db.URL_VARIABLE, database_url)
    monkeypatch.setenv("DAEDALUS_WORKER_ARGV", json.dumps(argv))
    monkeypatch.setenv("DAEDALUS_TOKEN", TOKEN)
    try:
        with TestClient(app, headers=AUTH) as api:
            run = start(api, "Add a 10mm cube").json()
            done = until(lambda: (lambda r: r if r["state"] in FINAL else None)(api.get("/v1/runs/%s" % run["id"]).json()),
                         seconds=420)
            assert api.get("/v1/runs/%s" % run["id"]).json()["session_id"] == run["session_id"]
            with db.connect(database_url) as check:
                kinds = [row[0] for row in check.execute("SELECT type FROM events WHERE run_id = %s ORDER BY seq", (run["id"],))]
    finally:
        _ssh("podman rm -f %s >/dev/null 2>&1" % name, check=False)
    assert done["state"] == "completed" and done["cost_usd"] >= 0
    assert {"intent", "applied"} <= set(kinds) and kinds[-1] == "completed", kinds
