"""
Level 2 -- the lane runs an editing fixture and always leaves a valid `output.step`:
the edit when it passes the guard, a copy of `input.step` when it does not.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import lane  # noqa: E402

pytestmark = pytest.mark.freecad


def _step(path, shape):
    shape.exportStep(path)
    return path


def _box():
    import Part

    return Part.makeBox(40, 30, 10)


def _box_with_a_hole():
    import FreeCAD
    import Part

    return _box().cut(Part.makeCylinder(5, 10, FreeCAD.Vector(20, 15, 0)))


def _two_boxes():
    import FreeCAD
    import Part

    return Part.makeCompound([_box(), Part.makeBox(5, 5, 5, FreeCAD.Vector(100, 0, 0))])


def _shrunk_box():
    import Part

    return Part.makeBox(4, 3, 1)


def test_an_edit_that_keeps_one_valid_solid_near_the_input_passes_the_guard(tmp_path):
    base = _step(str(tmp_path / "input.step"), _box())
    edit = _step(str(tmp_path / "edit.step"), _box_with_a_hole())

    assert lane.guard_edit(edit, base) is None


@pytest.mark.parametrize("build, word", [(_two_boxes, "solid"), (_shrunk_box, "volume")])
def test_an_edit_with_two_solids_or_a_shrunk_body_fails_the_guard(tmp_path, build, word):
    base = _step(str(tmp_path / "input.step"), _box())
    edit = _step(str(tmp_path / "edit.step"), build())

    assert word in lane.guard_edit(edit, base)


def test_a_file_with_no_solid_fails_the_guard(tmp_path):
    import Part

    base = _step(str(tmp_path / "input.step"), _box())
    edit = _step(str(tmp_path / "edit.step"), Part.makeLine((0, 0, 0), (1, 0, 0)))

    assert "no solid" in lane.guard_edit(edit, base)


def _fake_run(tmp_path, build=None, returncode=0):
    class FakeProcess:
        pass

    FakeProcess.returncode = returncode
    spawned = []

    def fake_run_one(label, sheet, model, effort, budget_usd, log, intent, provider, **kwargs):
        spawned.append({"label": label, "sheet": sheet, "intent": intent, **kwargs})
        open(log, "w").close()
        if build:
            _step(log[: -len(".jsonl")] + ".step", build())
        return FakeProcess()

    return fake_run_one, spawned


def _fixture(tmp_path, name="101"):
    folder = tmp_path / "data" / name
    folder.mkdir(parents=True)
    _step(str(folder / "input.step"), _box())
    (folder / "renders").mkdir()
    for view in ("front", "right", "top", "iso"):
        (folder / "renders" / (view + ".png")).write_bytes(b"png")
    (folder / "edit_description.txt").write_text("Cut a 10 mm bore.\n")
    return str(folder)


def _sweep(tmp_path, monkeypatch, folder, build=None, returncode=0):
    fake, spawned = _fake_run(tmp_path, build, returncode)
    monkeypatch.setattr(lane, "run_one", fake)
    monkeypatch.setattr(lane, "watch", lambda *a, **k: "exited")
    out = str(tmp_path / "out")
    lane.sweep([("101", "")], ["opus"], "medium", None, "unused", out, 1800, 1, False,
               "Claude (subscription)", None, edits={"101": folder})
    with open(os.path.join(out, "state.json"), encoding="utf-8") as handle:
        run = json.load(handle)["runs"]["101/opus"]
    return os.path.join(out, "opus", "101", "output.step"), run, spawned


def test_the_lane_runs_an_edit_from_the_input_step_with_its_description(tmp_path, monkeypatch):
    folder = _fixture(tmp_path)

    _output, _run, spawned = _sweep(tmp_path, monkeypatch, folder, _box_with_a_hole)

    assert spawned[0]["base_step"] == os.path.join(folder, "input.step")
    assert "Cut a 10 mm bore." in spawned[0]["intent"]
    assert spawned[0]["sheet"] == ""
    assert [os.path.basename(p) for p in spawned[0]["attach"]] == [
        "front.png", "right.png", "top.png", "iso.png"]


def test_a_passing_edit_is_the_output(tmp_path, monkeypatch):
    folder = _fixture(tmp_path)

    output, run, _spawned = _sweep(tmp_path, monkeypatch, folder, _box_with_a_hole)

    assert os.path.getsize(output) > 0
    assert open(output, "rb").read() != open(os.path.join(folder, "input.step"), "rb").read()
    assert run["status"] == "done"
    assert run["fallback"] is None


def test_a_two_solid_edit_ships_a_copy_of_the_input(tmp_path, monkeypatch):
    folder = _fixture(tmp_path)

    output, run, _spawned = _sweep(tmp_path, monkeypatch, folder, _two_boxes)

    assert open(output, "rb").read() == open(os.path.join(folder, "input.step"), "rb").read()
    assert "solid" in run["fallback"]


def test_a_run_with_no_solid_ships_a_copy_of_the_input(tmp_path, monkeypatch):
    folder = _fixture(tmp_path)

    output, run, _spawned = _sweep(tmp_path, monkeypatch, folder, None, returncode=3)

    assert open(output, "rb").read() == open(os.path.join(folder, "input.step"), "rb").read()
    assert run["fallback"] == "no solid"
    assert run["status"] == "failed"


def test_a_quota_stop_ships_nothing(tmp_path, monkeypatch):
    folder = _fixture(tmp_path)
    monkeypatch.setattr(lane, "quota_refusal", lambda log: "limit is spent")

    output, run, _spawned = _sweep(tmp_path, monkeypatch, folder, None, returncode=1)

    assert not os.path.exists(output)
    assert run["status"] == "quota"


def test_an_invalid_input_is_reported_in_the_state_and_on_the_console(
        tmp_path, monkeypatch, capsys):
    folder = _fixture(tmp_path)
    monkeypatch.setattr(lane, "step_problem", lambda path: "BRepCheck_UnorientableShape")

    _output, run, _spawned = _sweep(tmp_path, monkeypatch, folder, None, returncode=3)

    assert run["input_invalid"] == "BRepCheck_UnorientableShape"
    assert "INPUT INVALID 101" in capsys.readouterr().out


def _cube_with_a_needle():
    """A 10 mm cube whose top face is split by a needle triangle of zero height, which OCC calls invalid."""
    import FreeCAD
    import Part

    def polygon(points):
        return Part.Face(Part.makePolygon([FreeCAD.Vector(*p) for p in points + [points[0]]]))

    faces = [polygon([(0, 0, 0), (0, 10, 0), (10, 10, 0), (10, 0, 0)]),
             polygon([(0, 0, 0), (10, 0, 0), (10, 0, 10), (0, 0, 10)]),
             polygon([(10, 0, 0), (10, 10, 0), (10, 10, 10), (10, 0, 10)]),
             polygon([(10, 10, 0), (0, 10, 0), (0, 10, 10), (10, 10, 10)]),
             polygon([(0, 10, 0), (0, 0, 0), (0, 0, 10), (0, 10, 10)]),
             polygon([(0, 0, 10), (5, 0, 10), (10, 0, 10), (10, 10, 10), (0, 10, 10)]),
             polygon([(0, 0, 10), (10, 0, 10), (5, 0, 10)])]
    return Part.Solid(Part.Shell(faces))


def test_an_invalid_input_the_base_repair_mends_is_shipped_repaired_when_the_edit_fails(
        tmp_path, monkeypatch, capsys):
    folder = _fixture(tmp_path)
    _step(os.path.join(folder, "input.step"), _cube_with_a_needle())
    assert lane.step_problem(os.path.join(folder, "input.step"))

    output, run, _spawned = _sweep(tmp_path, monkeypatch, folder, None, returncode=3)

    assert lane.step_problem(output) is None
    assert run["input_invalid"] is None
    assert "INPUT INVALID" not in capsys.readouterr().out


def test_edit_task_pairs_an_id_with_its_fixture_folder(monkeypatch, tmp_path):
    folder = _fixture(tmp_path)
    seen = {}

    def fake_sweep(tasks, *args, **kwargs):
        seen.update(tasks=tasks, edits=kwargs["edits"])
        return False

    monkeypatch.setattr(lane, "sweep", fake_sweep)
    monkeypatch.setattr(sys, "argv", ["lane", "--model", "opus", "--edit-task", "101=" + folder,
                                      "--out", str(tmp_path / "out")])

    assert lane.main() == 0
    assert seen["tasks"] == [("101", "")]
    assert seen["edits"] == {"101": folder}


def test_run_one_hands_the_base_step_to_run_py(monkeypatch):
    seen = {}
    monkeypatch.setattr(lane.subprocess, "Popen", lambda cmd: seen.setdefault("cmd", cmd))

    lane.run_one("101", "", "opus", "medium", None, "/tmp/x.jsonl", "edit it", "p",
                 base_step="/d/input.step", attach=["/d/front.png", "/d/iso.png"])

    assert seen["cmd"][seen["cmd"].index("--base-step") + 1] == "/d/input.step"
    assert seen["cmd"][seen["cmd"].index("--attach") + 1:][:2] == ["/d/front.png", "/d/iso.png"]
    assert "--allow-user-objects" not in seen["cmd"]


def test_the_input_check_names_a_shape_the_official_gate_would_refuse(tmp_path):
    path = _step(str(tmp_path / "input.step"), _box())

    assert lane.step_problem(path) is None


def test_a_volume_change_of_a_real_edit_passes_and_only_an_absurd_one_fails(tmp_path):
    import FreeCAD
    import Part

    base = _step(str(tmp_path / "input.step"), _box())
    trimmed = _step(str(tmp_path / "trimmed.step"), _box().cut(
        Part.makeBox(40, 30, 1.8, FreeCAD.Vector(0, 0, 8.2))))
    grown = _step(str(tmp_path / "grown.step"), Part.makeBox(40, 30, 40))

    assert lane.guard_edit(trimmed, base) is None
    assert lane.guard_edit(grown, base) is None
