"""
A client cancels a Run and answers its questions: `:cancel` ends a Run in `cancelled`, `interactive` Runs wait in `needs-answer` until `:answer`, and an Owned Object `confirm` is refused unless the Run says otherwise.

The gateway tests run the app over `tests/fake_worker.py` against a real Postgres, skipped as in `test_service_postgres.py`.
The real-worker tests run `deploy/worker/scripted_worker.py` in the `dae-501-worker` image on the home server and skip when it is unreachable.
"""
import json
import subprocess
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from service import db, supervisor
from service.app import app
from service.owner import current_owner
from test_service_events import frames, server  # noqa: F401
from test_service_postgres import _admin_url, conn, database_url, repo  # noqa: F401
from test_service_runs import AUTH, TOKEN, api, final, start  # noqa: F401
from test_service_worker_channel import FAKE, HOST, ROOT, _ssh, alive, until


def state(api, run_id):
    return api.get("/v1/runs/%s" % run_id).json()["state"]


def kinds(database_url, run_id):
    with db.connect(database_url) as check:
        return [row for row in check.execute("SELECT type, payload FROM events WHERE run_id = %s ORDER BY seq", (run_id,))]


def test_cancelling_a_running_run_ends_it_cancelled_and_the_worker_stops_its_work(api, database_url):
    run = start(api, "slow").json()
    until(lambda: state(api, run["id"]) == "running" and kinds(database_url, run["id"]))
    cancelled = api.post("/v1/runs/%s:cancel" % run["id"])
    assert cancelled.status_code == 202 and cancelled.json()["id"] == run["id"]
    until(lambda: state(api, run["id"]) == "cancelled")
    seen = [kind for kind, _ in kinds(database_url, run["id"])]
    assert seen[-2:] == ["stopped", "cancelled"] or seen[-1] == "cancelled", seen
    again = start(api, "hello", session_id=run["session_id"])
    assert again.status_code == 202 and final(api, again.json()["id"])["state"] == "completed"


def test_a_worker_that_ignores_the_cancel_is_killed_and_the_run_still_ends_cancelled(api, database_url, monkeypatch):
    monkeypatch.setattr(supervisor, "CANCEL_TIMEOUT", 0.5)
    run = start(api, "hang").json()
    info = until(lambda: next((p for k, p in kinds(database_url, run["id"]) if k == "intent"), None))
    assert api.post("/v1/runs/%s:cancel" % run["id"]).status_code == 202
    until(lambda: state(api, run["id"]) == "cancelled", seconds=10)
    until(lambda: not alive(info["pid"]) and not alive(info["child"]), seconds=10)


def test_cancelling_a_run_in_a_final_state_or_an_unknown_one_is_refused_with_problem_json(api, repo):
    run = start(api, "hello").json()
    final(api, run["id"])
    done = api.post("/v1/runs/%s:cancel" % run["id"])
    missing = api.post("/v1/runs/run_missing:cancel")
    assert (done.status_code, missing.status_code) == (409, 404)
    for response in (done, missing):
        assert response.headers["content-type"] == "application/problem+json"
    app.dependency_overrides[current_owner] = lambda: repo.create_user()
    try:
        assert api.post("/v1/runs/%s:cancel" % run["id"]).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_an_interactive_run_waits_in_needs_answer_shows_its_question_in_the_stream_and_continues_after_answer(server, database_url):
    run = httpx.post(server + "/v1/runs", json={"prompt": "asks", "interactive": True}, headers=AUTH).json()
    assert run["interactive"] is True
    until(lambda: httpx.get("%s/v1/runs/%s" % (server, run["id"]), headers=AUTH).json()["state"] == "needs-answer")
    seen = []
    with httpx.stream("GET", "%s/v1/runs/%s/events" % (server, run["id"]), headers=AUTH, timeout=30) as response:
        for frame in frames(response.iter_lines()):
            seen.append(frame)
            if frame["event"] == "question":
                break
    assert json.loads(seen[-1]["data"]) == {"text": "solid or hollow?"}
    answered = httpx.post("%s/v1/runs/%s:answer" % (server, run["id"]), json={"answer": "solid"}, headers=AUTH)
    assert answered.status_code == 202
    until(lambda: httpx.get("%s/v1/runs/%s" % (server, run["id"]), headers=AUTH).json()["state"] == "completed")
    answers = [payload for kind, payload in kinds(database_url, run["id"]) if kind == "answer"]
    assert answers == [{"text": "solid", "by": "user"}]


def test_a_non_interactive_run_never_enters_needs_answer_even_when_the_worker_asks(api):
    seen = set()
    stop = threading.Event()
    run = start(api, "nags").json()
    assert run["interactive"] is False and run["confirm"] is False

    def watch():
        while not stop.is_set():
            seen.add(state(api, run["id"]))

    thread = threading.Thread(target=watch)
    thread.start()
    try:
        assert final(api, run["id"])["state"] == "completed"
    finally:
        stop.set()
        thread.join()
    assert "needs-answer" not in seen
    quiet = start(api, "asks").json()
    assert final(api, quiet["id"])["state"] == "completed"


def test_answer_on_a_run_that_is_not_waiting_is_409_problem_json(api):
    running = start(api, "slow").json()
    until(lambda: state(api, running["id"]) == "running")
    early = api.post("/v1/runs/%s:answer" % running["id"], json={"answer": "x"})
    api.post("/v1/runs/%s:cancel" % running["id"])
    until(lambda: state(api, running["id"]) == "cancelled")
    late = api.post("/v1/runs/%s:answer" % running["id"], json={"answer": "x"})
    missing = api.post("/v1/runs/run_missing:answer", json={"answer": "x"})
    empty = api.post("/v1/runs/%s:answer" % running["id"], json={})
    assert [r.status_code for r in (early, late, missing, empty)] == [409, 409, 404, 422]
    for response in (early, late, missing, empty):
        assert response.headers["content-type"] == "application/problem+json"


def test_a_run_is_not_interactive_and_does_not_confirm_unless_it_says_so(api, database_url):
    plain = start(api, "flags").json()
    both = start(api, "flags", interactive=True, confirm=True).json()
    final(api, plain["id"]), final(api, both["id"])
    sent = {run["id"]: [p for k, p in kinds(database_url, run["id"]) if k == "flags"][0] for run in (plain, both)}
    assert sent[plain["id"]] == {"interactive": False, "confirm": False}
    assert sent[both["id"]] == {"interactive": True, "confirm": True}
    assert api.get("/v1/runs/%s" % both["id"]).json()["confirm"] is True


IMAGE = "dae-501-worker"
REMOTE = "dae-501-build"


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


@pytest.fixture
def real(worker_image, database_url, monkeypatch):
    names = []

    def launch(script, delay=0):
        name = "dae-501-%s-%d" % (script, len(names))
        _ssh("podman rm -f %s >/dev/null 2>&1" % name, check=False)
        names.append(name)
        argv = ["ssh", "-o", "BatchMode=yes", HOST, "podman run -i --rm --name %s -e DAE_SCRIPT=%s -e DAE_DELAY=%s %s "
                "python deploy/worker/scripted_worker.py" % (name, script, delay, worker_image)]
        monkeypatch.setenv(db.URL_VARIABLE, database_url)
        monkeypatch.setenv("DAEDALUS_WORKER_ARGV", json.dumps(argv))
        monkeypatch.setenv("DAEDALUS_TOKEN", TOKEN)
        return TestClient(app, headers=AUTH)

    yield launch
    for name in names:
        _ssh("podman rm -f %s >/dev/null 2>&1" % name, check=False)


def texts(database_url, run_id, kind):
    return [payload.get("text") for seen, payload in kinds(database_url, run_id) if seen == kind]


def test_a_real_interactive_run_waits_for_the_answer_and_builds_the_cube_after_it(real, database_url):
    with real("ask") as api:
        run = start(api, "Add a 10mm cube", interactive=True).json()
        until(lambda: state(api, run["id"]) == "needs-answer", seconds=420)
        assert texts(database_url, run["id"], "question") == ["solid or hollow?"]
        assert api.post("/v1/runs/%s:answer" % run["id"], json={"answer": "solid"}, headers=AUTH).status_code == 202
        until(lambda: state(api, run["id"]) in ("completed", "failed", "blocked"), seconds=420)
        assert state(api, run["id"]) == "completed"
    assert texts(database_url, run["id"], "answer")[0] == "solid"
    assert "applied" in [kind for kind, _ in kinds(database_url, run["id"])]


def test_a_real_non_interactive_run_decides_alone_and_never_enters_needs_answer(real, database_url):
    seen = set()
    with real("ask") as api:
        run = start(api, "Add a 10mm cube").json()
        until(lambda: seen.add(state(api, run["id"])) or state(api, run["id"]) in ("completed", "failed", "blocked"), seconds=420)
        assert state(api, run["id"]) == "completed"
    assert "needs-answer" not in seen
    with db.connect(database_url) as check:
        answers = [row[0] for row in check.execute("SELECT payload FROM events WHERE run_id = %s AND type = 'answer'", (run["id"],))]
    assert answers and answers[0]["by"] == "nobody"


def test_a_real_run_cancelled_mid_run_ends_cancelled_by_the_worker_itself(real, database_url):
    with real("cube", delay=4) as api:
        run = start(api, "Add a 10mm cube").json()
        until(lambda: kinds(database_url, run["id"]), seconds=420)
        assert api.post("/v1/runs/%s:cancel" % run["id"]).status_code == 202
        until(lambda: state(api, run["id"]) == "cancelled", seconds=120)
    seen = kinds(database_url, run["id"])
    assert seen[-1] == ("cancelled", {"reason": "cancelled by the client"}), seen[-3:]
    assert "applied" not in [kind for kind, _ in seen]


def test_a_real_owned_object_confirm_is_refused_unless_the_run_overrides_it(real, database_url):
    ended = ("completed", "failed", "blocked", "stopped", "budget_exhausted")
    with real("delete") as api:
        refused = start(api, "Remove the box").json()
        until(lambda: state(api, refused["id"]) in ended, seconds=420)
    with real("delete") as api:
        allowed = start(api, "Remove the box", confirm=True).json()
        until(lambda: state(api, allowed["id"]) in ended, seconds=420)
    assert texts(database_url, refused["id"], "answer")[0].startswith("You refused to let it delete UserBox")
    assert texts(database_url, allowed["id"], "answer")[0].startswith("You allowed it to delete UserBox")


def test_a_cancelled_interactive_run_still_carries_its_attachment_and_flags_and_leaves_its_blobs(tmp_path, database_url, monkeypatch, repo, conn):
    from test_service_blobs import row
    owner = repo.create_user()
    monkeypatch.setenv(db.URL_VARIABLE, database_url)
    monkeypatch.setenv("DAEDALUS_BLOB_ROOT", str(tmp_path / "blobs"))
    monkeypatch.setenv("DAEDALUS_WORKER_ARGV", json.dumps(FAKE))
    monkeypatch.setenv("DAEDALUS_TOKEN", TOKEN)
    app.dependency_overrides[current_owner] = lambda: owner
    try:
        with TestClient(app, headers=AUTH) as api:
            blob = api.post("/v1/blobs", params={"name": "drawing.png"}, content=b"\x89PNG-bytes").json()
            run = start(api, "carry", attachments=[blob["id"]], interactive=True, confirm=True).json()
            until(lambda: state(api, run["id"]) == "running" and kinds(database_url, run["id"]))
            assert api.post("/v1/runs/%s:cancel" % run["id"]).status_code == 202
            until(lambda: state(api, run["id"]) == "cancelled")
    finally:
        app.dependency_overrides.clear()
    carried = [payload for kind, payload in kinds(database_url, run["id"]) if kind == "carried"][0]
    assert carried == {"files": [{"name": "drawing.png", "sha256": blob["sha256"]}], "interactive": True, "confirm": True}
    assert row(conn, run["id"], "step") is not None and row(conn, run["id"], "checkpoint") is not None
