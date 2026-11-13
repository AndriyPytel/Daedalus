"""
One real Run in the worker image on the home server gets past `No model selected`: the provider keys come from a podman secret that becomes the worker's environment, and the model comes from the owner's preferences through the channel.

It skips unless the server is reachable, the podman secret `dae-503-keys` (a JSON `{provider: key}`) exists there, and `DAE_REAL_PROVIDER` and `DAE_REAL_MODEL` name the model it may spend on.
"""
import json
import os
import subprocess

import pytest
from fastapi.testclient import TestClient

from service import db
from service.app import app
from test_service_postgres import _admin_url, conn, database_url, repo  # noqa: F401
from test_service_runs import AUTH, FINAL, TOKEN
from test_service_worker_channel import HOST, ROOT, _ssh, until

IMAGE = "dae-503-worker"
REMOTE = "dae-503-build"
SECRET = "dae-503-keys"


@pytest.fixture(scope="module")
def worker_image():
    probe = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", HOST,
                            "podman secret inspect %s >/dev/null" % SECRET], capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("no podman secret %s on %s: %s" % (SECRET, HOST, probe.stderr.strip()[-200:]))
    if not (os.environ.get("DAE_REAL_PROVIDER") and os.environ.get("DAE_REAL_MODEL")):
        pytest.skip("DAE_REAL_PROVIDER and DAE_REAL_MODEL are not set")
    _ssh("rm -rf ~/%s/ctx && mkdir -p ~/%s/ctx" % (REMOTE, REMOTE))
    tar = subprocess.Popen(["tar", "--exclude=__pycache__", "-cf", "-", "-C", ROOT, "src", "config", "deploy"],
                           stdout=subprocess.PIPE)
    sent = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, "tar -xf - -C ~/%s/ctx" % REMOTE],
                          stdin=tar.stdout, capture_output=True)
    tar.stdout.close()
    assert tar.wait() == 0 and sent.returncode == 0, sent.stderr
    _ssh("cd ~/%s/ctx && podman build -t %s -f deploy/worker/Containerfile ." % (REMOTE, IMAGE), timeout=2400)
    return IMAGE


def test_a_real_run_with_server_secrets_and_the_owners_model_produces_events(worker_image, database_url, monkeypatch):
    name = "dae-503-real"
    _ssh("podman rm -f %s >/dev/null 2>&1" % name, check=False)
    argv = ["ssh", "-o", "BatchMode=yes", HOST,
            "podman run -i --rm --name %s --secret %s,type=env,target=DAEDALUS_API_KEYS %s python -m service.worker"
            % (name, SECRET, worker_image)]
    monkeypatch.setenv(db.URL_VARIABLE, database_url)
    monkeypatch.setenv("DAEDALUS_WORKER_ARGV", json.dumps(argv))
    monkeypatch.setenv("DAEDALUS_TOKEN", TOKEN)
    choice = {"provider": os.environ["DAE_REAL_PROVIDER"], "model": os.environ["DAE_REAL_MODEL"]}
    try:
        with TestClient(app, headers=AUTH) as api:
            api.patch("/v1/users/usr_owner/preferences", json={**choice, "budget_usd": 0.25})
            run = api.post("/v1/runs", json={"prompt": "Add a 10mm cube"}).json()
            done = until(lambda: (lambda r: r if r["state"] in FINAL else None)(api.get("/v1/runs/%s" % run["id"]).json()),
                         seconds=420)
            with db.connect(database_url) as check:
                events = [(row[0], row[1]) for row in check.execute(
                    "SELECT type, payload FROM events WHERE run_id = %s ORDER BY seq", (run["id"],))]
    finally:
        _ssh("podman rm -f %s >/dev/null 2>&1" % name, check=False)
    assert events, done
    assert "No model selected" not in json.dumps(events)
    assert "intent" in [kind for kind, _ in events], events
