"""
The worker-host supervisor and the gateway's relay carry the worker channel over one TCP connection per worker, so a forced kill on the gateway side still ends the worker process and everything it started.

The tests run the supervisor in this process against `fake_worker.py` and the relay as a real subprocess, as the gateway launches it.
"""
import json
import os
import signal
import subprocess
import sys

import pytest

from service import workerhost
from test_service_worker_channel import alive, until

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = [sys.executable, os.path.join(HERE, "fake_worker.py")]


@pytest.fixture
def host():
    server = workerhost.listen(0, FAKE, grace=0.5, address="127.0.0.1")
    yield "127.0.0.1:%d" % server.getsockname()[1]
    server.close()


def relay(address):
    return subprocess.Popen([sys.executable, "-m", "service.relay", address], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            env={**os.environ, "PYTHONPATH": os.path.join(os.path.dirname(HERE), "src")})


def say(process, **message):
    process.stdin.write((json.dumps(message) + "\n").encode())
    process.stdin.flush()


def hear(process):
    return json.loads(process.stdout.readline())


def start(address, prompt):
    process = relay(address)
    assert hear(process) == {"op": "ready"}
    say(process, op="send", prompt=prompt)
    return process, hear(process)["data"]


def test_a_line_sent_through_the_relay_comes_back_from_the_worker_behind_it(host):
    process, data = start(host, "pid")
    try:
        assert data["pid"] != process.pid
        assert hear(process) == {"op": "done"}
    finally:
        process.kill()


def test_closing_the_relay_input_ends_the_worker_and_the_relay_exits(host):
    process, data = start(host, "pid")
    process.stdin.close()
    assert process.wait(10) == 0
    until(lambda: not alive(data["pid"]))


def test_a_sigkilled_relay_leaves_no_worker_and_no_child_of_the_worker(host):
    process, data = start(host, "hang")
    assert alive(data["pid"]) and alive(data["child"])
    process.send_signal(signal.SIGKILL)
    process.wait()
    until(lambda: not alive(data["pid"]) and not alive(data["child"]))


def test_two_connections_are_two_workers(host):
    first, one = start(host, "pid")
    second, two = start(host, "pid")
    try:
        assert one["pid"] != two["pid"]
        assert alive(one["pid"]) and alive(two["pid"])
    finally:
        first.kill()
        second.kill()
    until(lambda: not alive(one["pid"]) and not alive(two["pid"]))


def test_a_worker_that_exits_closes_its_connection_and_the_relay(host):
    process = relay(host)
    assert hear(process) == {"op": "ready"}
    say(process, op="send", prompt="bye")
    assert process.wait(10) == 0
