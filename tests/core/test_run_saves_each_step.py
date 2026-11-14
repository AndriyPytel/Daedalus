"""
Level 2 -- a run saves its STEP after every applied Step, so a run killed by the lane's wall-clock limit still leaves the build it had.
"""
import json
import os
import signal
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import run  # noqa: E402
from core.events import APPLIED, EventStream  # noqa: E402


class _Client:
    def __init__(self, solid=True, fail=False):
        self.events = EventStream()
        self.solid = solid
        self.fail = fail
        self.exported = []

    def watch(self, callback):
        self.events.subscribe(callback)

    def has_solid(self):
        return self.solid

    def export_step(self, path, only=None):
        with open(path, "w") as handle:
            handle.write("PARTIAL" if self.fail else "STEP %d" % (len(self.exported) + 1))
        if self.fail:
            raise RuntimeError("the export died")
        self.exported.append(path)
        return path


def test_every_applied_step_rewrites_the_save_in_place(tmp_path):
    client = _Client()
    path = str(tmp_path / "made.step")
    run.save_each_step(client, path)

    client.events.emit(APPLIED, step=1)
    client.events.emit(APPLIED, step=2)

    assert open(path).read() == "STEP 2"
    assert os.listdir(tmp_path) == ["made.step"]


def test_a_step_that_leaves_no_solid_writes_nothing(tmp_path):
    client = _Client(solid=False)
    run.save_each_step(client, str(tmp_path / "made.step"))

    client.events.emit(APPLIED, step=1)

    assert os.listdir(tmp_path) == []


def test_every_applied_step_is_recorded_once_its_step_is_written(tmp_path):
    client = _Client()
    path = str(tmp_path / "made.step")
    recorded = []
    run.save_each_step(client, path, record=lambda saved: recorded.append(open(saved).read()))

    client.events.emit(APPLIED, step=1)
    client.events.emit(APPLIED, step=2)

    assert recorded == ["STEP 1", "STEP 2"]


def test_a_save_that_is_not_a_step_is_left_to_the_final_export(tmp_path):
    client = _Client()
    run.save_each_step(client, str(tmp_path / "made.FCStd"))

    client.events.emit(APPLIED, step=1)

    assert client.exported == []


def test_an_export_that_dies_leaves_the_earlier_step_whole_and_no_partial_file(tmp_path):
    path = str(tmp_path / "made.step")
    with open(path, "w") as handle:
        handle.write("EARLIER")

    with pytest.raises(RuntimeError):
        run.write_step(_Client(fail=True), path)

    assert open(path).read() == "EARLIER"
    assert os.listdir(tmp_path) == ["made.step"]


def test_the_final_export_is_written_through_the_same_atomic_write(tmp_path):
    client = _Client()
    path = str(tmp_path / "made.step")

    run.finish(client, type("R", (), {"ok": True})(), path)

    assert open(path).read() == "STEP 1"
    assert os.listdir(tmp_path) == ["made.step"]


DRIVER = """
import json, os, signal, sys
sys.path.insert(0, {tools!r})
import run
from core.llm import ScriptedLLMClient


class DiesAtTheThirdCall(ScriptedLLMClient):
    def stream(self, messages, **overrides):
        if len(self.calls) == 2:
            os.kill(os.getpid(), signal.SIGKILL)
        return super().stream(messages, **overrides)


def box(name):
    return json.dumps({{"code": "box = doc.addObject('Part::Box', '%s')\\nbox.Placement.Base.x = %d\\ndoc.recompute()" % (name, 20 * len(name))}})


import sdk
sdk.client_for = lambda *args: DiesAtTheThirdCall([box("A"), box("BB")])
sys.argv = ["run.py", "--intent", "Make two boxes.", "--model", "scripted", "--log", {log!r}, "--save", {save!r}]
run.main()
"""


@pytest.mark.freecad
def test_a_run_killed_after_two_applied_steps_leaves_a_step_holding_them(tmp_path):
    log, save = str(tmp_path / "run.jsonl"), str(tmp_path / "run.step")
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER.format(tools=os.path.join(ROOT, "src", "tools"), log=log, save=save))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))

    process = subprocess.run([sys.executable, str(driver)], env=env, capture_output=True, text=True)

    assert process.returncode == -signal.SIGKILL, process.stderr[-2000:]
    applied = [line for line in open(log) if json.loads(line)["kind"] == APPLIED]
    assert len(applied) == 2
    import Part

    shape = Part.read(save)
    assert len(shape.Solids) == 2
    assert json.load(open(str(tmp_path / "run.json")))["solids"] == 2
    assert set(os.listdir(tmp_path)) == {"driver.py", "run.jsonl", "run.step", "run.json"}
