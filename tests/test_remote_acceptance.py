"""
The epic's three acceptance criteria, from this machine against a deployed gateway.

They skip unless `DAE_REMOTE_URL` (for example `http://127.0.0.1:8505` through `ssh -L`, or the Tailscale address) and `DAE_REMOTE_TOKEN` are set. The numbers each test measures are written as JSON to `DAE_REMOTE_OUT` (default `<tmp>/dae-remote`), and the idle-TTL wait follows the gateway's `DAEDALUS_IDLE_TTL`, which `DAE_REMOTE_IDLE_TTL` restates (default 900).
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

import modelmania_harness
from conftest import HARNESS_PYTHON
from test_service_events import frames
from test_service_runs import FINAL
from test_service_worker_channel import until

pytestmark = [pytest.mark.remote, pytest.mark.timeout(2400)]

ROOT = Path(__file__).parent.parent
SHEET = "model-mania-2024.pdf"
INTENT = "Build the model from the attached drawing."
RUN_SECONDS = 1800
POLL_SECONDS = 5
IDLE_TTL = float(os.environ.get("DAE_REMOTE_IDLE_TTL", "900"))
OUT = Path(os.environ.get("DAE_REMOTE_OUT") or Path(tempfile.gettempdir()) / "dae-remote")


@pytest.fixture(scope="module")
def api():
    with httpx.Client(base_url=os.environ["DAE_REMOTE_URL"].rstrip("/") + "/v1",
                      headers={"Authorization": "Bearer " + os.environ["DAE_REMOTE_TOKEN"]}, timeout=60) as client:
        yield client


def record(name, numbers):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / (name + ".json")).write_text(json.dumps(numbers, indent=2, sort_keys=True), encoding="utf-8")


def finished(api, run_id, seconds=RUN_SECONDS):
    return until(lambda: (lambda run: run if run["state"] in FINAL else None)(api.get("/runs/%s" % run_id).json()),
                 seconds, every=POLL_SECONDS)


def validity(step):
    done = subprocess.run([HARNESS_PYTHON, str(ROOT / "harness" / "validity.py"), str(step)],
                          capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


@pytest.mark.cadgenbench
@pytest.mark.freecad
@pytest.mark.timeout(2 * RUN_SECONDS + 600)
def test_ac1_the_remote_step_is_as_valid_and_manifold_as_the_local_one(api, tmp_path):
    sheet = modelmania_harness.SHEETS[SHEET]
    if not sheet.exists():
        pytest.skip("sheet not found: %s; set DAE_MODELMANIA_DIR" % sheet)
    choice = {"provider": "Claude (subscription)", "model": "sonnet", "budget_usd": 20}

    blob = api.post("/blobs", params={"name": SHEET}, content=sheet.read_bytes()).json()
    started = time.time()
    run = api.post("/runs", json={"prompt": INTENT, "attachments": [blob["id"]], "overrides": choice}).json()
    done = finished(api, run["id"])
    remote_seconds = time.time() - started
    remote_step = tmp_path / "remote.step"
    download = api.get("/runs/%s/step" % run["id"])
    assert download.status_code == 200, done
    remote_step.write_bytes(download.content)

    local_step = tmp_path / "local.step"
    subprocess.run([sys.executable, str(ROOT / "src" / "tools" / "run.py"), "--task", str(sheet), "--intent", INTENT,
                    "--provider", choice["provider"], "--model", choice["model"],
                    "--budget-usd", str(choice["budget_usd"]),
                    "--log", str(tmp_path / "local.jsonl"), "--save", str(local_step)], check=True, timeout=RUN_SECONDS)
    local = json.loads(local_step.with_suffix(".json").read_text(encoding="utf-8"))

    remote = validity(remote_step)
    record("ac1", {"remote": {**remote, "state": done["state"], "cost_usd": done["cost_usd"], "seconds": remote_seconds},
                   "local": local, "local_valid_by_analyze_step": validity(local_step)["is_valid"]})
    assert remote["is_valid"] and remote["is_watertight"], remote["errors"]
    assert local["valid"], local


def test_ac2_an_edit_run_after_the_idle_ttl_stop_continues_the_same_session(api):
    first = api.post("/runs", json={"prompt": "Add a 10mm cube", "overrides": {"budget_usd": 1}}).json()
    assert finished(api, first["id"], 600)["state"] == "completed"
    until(lambda: api.get("/sessions/%s" % first["session_id"]).json()["state"] == "idle", IDLE_TTL + 300,
          every=POLL_SECONDS)

    second = api.post("/runs", json={"prompt": "Make the cube 20mm", "session_id": first["session_id"],
                                     "overrides": {"budget_usd": 1}}).json()
    done = finished(api, second["id"], 600)
    step = api.get("/runs/%s/step" % second["id"])
    record("ac2", {"idle_ttl": IDLE_TTL, "first": first["id"], "second": second["id"], "state": done["state"],
                   "cost_usd": done["cost_usd"], "step_status": step.status_code})
    assert done["state"] == "completed", done
    assert step.status_code == 200


def sse(api, run_id, last=None, stop_after=None):
    headers = {"Last-Event-ID": str(last)} if last is not None else {}
    got = []
    with api.stream("GET", "/runs/%s/events" % run_id, headers=headers, timeout=None) as response:
        assert response.status_code == 200
        for frame in frames(response.iter_lines()):
            got.append(frame)
            if stop_after is not None and len(got) == stop_after:
                break
    return got


def test_ac3_an_sse_break_and_reconnect_loses_and_repeats_no_event_id(api):
    run = api.post("/runs", json={"prompt": "Add a 10mm cube", "overrides": {"budget_usd": 1}}).json()
    before = sse(api, run["id"], stop_after=3)
    last = before[-1]["id"]
    after = sse(api, run["id"], last=last)
    ids = [int(frame["id"]) for frame in before + after]
    finished(api, run["id"], 600)
    whole = [int(frame["id"]) for frame in sse(api, run["id"])]
    record("ac3", {"run": run["id"], "before_break": len(before), "after_reconnect": len(after), "whole": len(whole)})
    assert len(ids) == len(set(ids))
    assert ids == whole
