"""
A client cancels a Run and answers its questions: `:cancel` ends a Run in `cancelled`, `interactive` Runs wait in `needs-answer` until `:answer`, and an Owned Object `confirm` is refused unless the Run says otherwise.

The gateway tests run the app over `tests/fake_worker.py` against a real Postgres, skipped as in `test_service_postgres.py`.
The real-worker tests run `deploy/worker/scripted_worker.py` in the `dae-501-worker` image on the home server and skip when it is unreachable.
"""
import json
import threading

import httpx

from service import db, supervisor
from service.app import app
from service.owner import current_owner
from test_service_events import frames, server  # noqa: F401
from test_service_postgres import _admin_url, conn, database_url, repo  # noqa: F401
from test_service_runs import api, final, start  # noqa: F401
from test_service_worker_channel import alive, until


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
    run = httpx.post(server + "/v1/runs", json={"prompt": "asks", "interactive": True}).json()
    assert run["interactive"] is True
    until(lambda: httpx.get("%s/v1/runs/%s" % (server, run["id"])).json()["state"] == "needs-answer")
    seen = []
    with httpx.stream("GET", "%s/v1/runs/%s/events" % (server, run["id"]), timeout=30) as response:
        for frame in frames(response.iter_lines()):
            seen.append(frame)
            if frame["event"] == "question":
                break
    assert json.loads(seen[-1]["data"]) == {"text": "solid or hollow?"}
    answered = httpx.post("%s/v1/runs/%s:answer" % (server, run["id"]), json={"answer": "solid"})
    assert answered.status_code == 202
    until(lambda: httpx.get("%s/v1/runs/%s" % (server, run["id"])).json()["state"] == "completed")
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
