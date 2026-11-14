"""
Level 1 -- the lane runs an edit fixture on an injected run and always leaves an `output.step`:
the edit when its volume is within range of the base, the base it started from when it is not.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import lane  # noqa: E402
from tests.core.test_lane import _quota_log  # noqa: E402


def _fake_run(step=None, result=None, base="BASE", returncode=0):
    class FakeProcess:
        pass

    FakeProcess.returncode = returncode
    spawned = []

    def fake_run_one(label, path, model, effort, budget_usd, log, intent, provider, native=False):
        spawned.append({"label": label, "path": path})
        stem = log[: -len(".jsonl")]
        open(log, "w").close()
        if step is not None:
            open(stem + ".step", "w").write(step)
        if result is not None:
            open(stem + ".json", "w").write(json.dumps(result))
        if base is not None:
            open(stem + ".base.step", "w").write(base)
        return FakeProcess()

    return fake_run_one, spawned


def _fixture(tmp_path):
    folder = tmp_path / "data" / "101"
    folder.mkdir(parents=True)
    (folder / "input.step").write_text("INPUT")
    (folder / "edit_description.txt").write_text("Cut a 10 mm bore.\n")
    return str(folder)


def _sweep(tmp_path, folder, run_one, outcome="exited"):
    out = str(tmp_path / "out")
    lane.sweep([("101", folder)], ["opus"], "medium", None, "unused", out, 1800, 1, False,
               "Claude (subscription)", None, run_one=run_one, watch=lambda *a, **k: outcome)
    with open(os.path.join(out, "state.json"), encoding="utf-8") as handle:
        run = json.load(handle)["runs"]["101/opus"]
    return os.path.join(out, "opus", "101", "output.step"), run


IN_RANGE = {"solids": 1, "valid": True, "volume": 900.0, "base_volume": 1000.0}


def test_the_lane_runs_an_edit_on_its_fixture_folder(tmp_path):
    folder = _fixture(tmp_path)
    run_one, spawned = _fake_run("EDIT", IN_RANGE)

    _sweep(tmp_path, folder, run_one)

    assert spawned == [{"label": "101", "path": folder}]


def test_a_volume_ratio_inside_the_range_ships_the_runs_step(tmp_path):
    run_one, _ = _fake_run("EDIT", IN_RANGE)

    output, run = _sweep(tmp_path, _fixture(tmp_path), run_one)

    assert open(output).read() == "EDIT"
    assert run["status"] == "done"
    assert run["fallback"] is None


def test_a_volume_ratio_outside_range_ships_the_base(tmp_path):
    shrunk = {**IN_RANGE, "volume": 100.0}
    run_one, _ = _fake_run("EDIT", shrunk)

    output, run = _sweep(tmp_path, _fixture(tmp_path), run_one)

    assert open(output).read() == "BASE"
    assert run["fallback"] == "volume ratio 0.100"


def test_a_ratio_is_measured_from_the_numbers_alone():
    assert lane.guard_edit({"volume": 5000.0, "base_volume": 1000.0}) is None
    assert "volume ratio" in lane.guard_edit({"volume": 5001.0, "base_volume": 1000.0})
    assert lane.guard_edit({"volume": 0.0, "base_volume": 1000.0}) == "volume ratio 0.000"
    assert lane.guard_edit({"volume": 3.0, "base_volume": None}) is None


def test_a_run_cut_at_the_time_ceiling_ships_the_step_it_saved(tmp_path):
    run_one, _ = _fake_run("EDIT", IN_RANGE, returncode=-9)

    output, run = _sweep(tmp_path, _fixture(tmp_path), run_one, outcome="over time")

    assert run["status"] == "ceiling"
    assert run["fallback"] is None
    assert open(output).read() == "EDIT"


def test_a_run_with_no_solid_ships_the_base(tmp_path):
    run_one, _ = _fake_run(returncode=3)

    output, run = _sweep(tmp_path, _fixture(tmp_path), run_one)

    assert open(output).read() == "BASE"
    assert run["fallback"] == "no solid"
    assert run["status"] == "failed"


def test_a_step_with_no_result_beside_it_ships_the_base(tmp_path):
    run_one, _ = _fake_run("EDIT")

    output, run = _sweep(tmp_path, _fixture(tmp_path), run_one)

    assert open(output).read() == "BASE"
    assert run["fallback"] == "no solid"


def test_a_run_that_saved_no_base_ships_a_copy_of_the_fixture_input(tmp_path):
    run_one, _ = _fake_run(returncode=1, base=None)

    output, _run = _sweep(tmp_path, _fixture(tmp_path), run_one)

    assert open(output).read() == "INPUT"


def test_a_quota_stop_ships_nothing(tmp_path):
    def run_one(label, path, model, effort, budget_usd, log, intent, provider, native=False):
        _quota_log(log)
        return type("Process", (), {"returncode": 1})()

    output, run = _sweep(tmp_path, _fixture(tmp_path), run_one)

    assert not os.path.exists(output)
    assert run["status"] == "quota"


def test_edit_task_pairs_an_id_with_its_fixture_folder(monkeypatch, tmp_path):
    folder = _fixture(tmp_path)
    seen = {}

    def fake_sweep(tasks, *args, **kwargs):
        seen.update(tasks=tasks)
        return False

    monkeypatch.setattr(sys, "argv", ["lane", "--model", "opus", "--edit-task", "101=" + folder,
                                      "--budget-usd", "5", "--out", str(tmp_path / "out")])

    assert lane.main(sweep=fake_sweep) == 0
    assert seen["tasks"] == [["101", folder]]
