"""
The gateway drives one worker process per Session over JSON lines on a plain stdin/stdout pipe.

The channel, crash and stop tests run a fake worker script with no FreeCAD against a real Postgres, skipped as in
`test_service_postgres.py`. The one real-worker test runs `service.worker` in the `dae-498-worker` image on the home
server over ssh and `podman run -i`, and skips when the server is unreachable.
"""
import os
import signal
import subprocess
import sys
import time

import pytest

from service import db
from service.supervisor import Worker
from test_service_postgres import _admin_url, conn, database_url, repo  # noqa: F401

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FAKE = [sys.executable, os.path.join(HERE, "fake_worker.py")]
HOST = os.environ.get("DAE_SERVER", "andrii@192.168.0.150")
IMAGE = "dae-498-worker"
REMOTE = "dae-498-build"


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return "Z" not in subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout
    except OSError:
        return True


def until(check, seconds=15):
    deadline = time.time() + seconds
    while time.time() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("not true within %ss" % seconds)


@pytest.fixture
def workers(database_url, repo):
    started = []
    owner = repo.create_user()

    def make(argv=FAKE):
        session = repo.create_session(owner)
        worker = Worker(argv, database_url, owner, session["id"])
        worker.start()
        started.append(worker)
        return worker

    make.owner = owner
    yield make
    for worker in started:
        worker.stop(timeout=3)


def state(repo, owner, run_id):
    return repo.get_run(owner, run_id)["state"]


def kinds(repo, owner, run_id):
    return [event["type"] for event in repo.list_events(owner, run_id)]


def test_a_prompt_sent_to_a_spawned_worker_yields_its_events_in_order(workers, repo):
    worker = workers()
    run_id = worker.send("hello")
    until(lambda: state(repo, workers.owner, run_id) == "completed")
    events = repo.list_events(workers.owner, run_id)
    assert [event["type"] for event in events] == ["intent", "completed"]
    assert events[0]["payload"] == {"prompt": "hello"}
    assert [event["seq"] for event in events] == sorted(event["seq"] for event in events)


def test_a_second_prompt_to_the_same_worker_is_a_second_run_with_its_own_events(workers, repo):
    worker = workers()
    first = worker.send("hello")
    until(lambda: state(repo, workers.owner, first) == "completed")
    second = worker.send("hello")
    until(lambda: state(repo, workers.owner, second) == "completed")
    assert first != second
    assert kinds(repo, workers.owner, second) == ["intent", "completed"]


def test_every_stored_event_notifies_the_events_channel(workers, repo, database_url):
    with db.connect(database_url) as listener:
        listener.execute("LISTEN events")
        worker = workers()
        run_id = worker.send("hello")
        until(lambda: state(repo, workers.owner, run_id) == "completed")
        seen = [notice.payload for notice in listener.notifies(timeout=2, stop_after=2)]
    assert seen == [str(event["seq"]) for event in repo.list_events(workers.owner, run_id)]


def test_a_worker_error_fails_its_run_and_stores_the_message(workers, repo):
    worker = workers()
    run_id = worker.send("boom")
    until(lambda: state(repo, workers.owner, run_id) == "failed")
    last = repo.list_events(workers.owner, run_id)[-1]
    assert last["type"] == "error" and last["payload"] == {"message": "provider refused"}
    assert worker.alive


def test_killing_a_worker_fails_the_active_run_of_its_session_and_leaves_another_session_running(workers, repo):
    victim, bystander = workers(), workers()
    run_id = victim.send("hang")
    until(lambda: kinds(repo, workers.owner, run_id) == ["intent"])
    other = bystander.send("hang")
    until(lambda: kinds(repo, workers.owner, other) == ["intent"])
    os.kill(victim.pid, signal.SIGKILL)
    until(lambda: state(repo, workers.owner, run_id) == "failed")
    assert kinds(repo, workers.owner, run_id)[-1] == "failed"
    assert state(repo, workers.owner, other) == "running"
    assert bystander.alive and alive(bystander.pid) and not victim.alive


def test_a_worker_that_exits_with_no_active_run_leaves_no_failed_run(workers, repo):
    worker = workers()
    first = worker.send("hello")
    until(lambda: state(repo, workers.owner, first) == "completed")
    worker.stop()
    assert state(repo, workers.owner, first) == "completed"
    assert not worker.alive


def test_stopping_a_worker_ends_the_process_and_its_children_with_no_orphan(workers, repo):
    worker = workers()
    run_id = worker.send("hang")
    event = until(lambda: next(iter(repo.list_events(workers.owner, run_id)), None))
    pid, child = event["payload"]["pid"], event["payload"]["child"]
    assert alive(pid) and alive(child)
    worker.stop(timeout=1)
    until(lambda: not alive(pid) and not alive(child), seconds=5)
    assert not worker.alive
    assert state(repo, workers.owner, run_id) == "failed"


def test_stopping_an_idle_worker_closes_its_pipe_and_it_exits_by_itself(workers):
    worker = workers()
    pid = worker.pid
    worker.stop(timeout=5)
    assert not alive(pid)


def test_a_truncated_last_line_is_dropped_not_stored(workers, repo):
    worker = workers()
    run_id = worker.send("cut")
    until(lambda: state(repo, workers.owner, run_id) == "failed")
    assert kinds(repo, workers.owner, run_id) == ["intent", "failed"]


def test_a_worker_that_dies_before_it_is_ready_fails_start_instead_of_hanging(database_url, repo):
    owner = repo.create_user()
    session = repo.create_session(owner)
    worker = Worker([sys.executable, "-c", "import sys; sys.exit(7)"], database_url, owner, session["id"])
    with pytest.raises(RuntimeError):
        worker.start()


def _ssh(command, timeout=600, check=True):
    done = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", HOST, command],
                          capture_output=True, text=True, timeout=timeout)
    if check:
        assert done.returncode == 0, "%s\n%s%s" % (command, done.stdout[-3000:], done.stderr[-3000:])
    return done


