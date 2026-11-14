"""
Level 2 -- `run.py --task` runs a task folder or a bare Intent through a real kernel and leaves, beside the saved STEP, its numbers and the base it started from.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import run  # noqa: E402
from core.llm import ScriptedLLMClient  # noqa: E402
from chat_helpers import say  # noqa: E402

pytestmark = pytest.mark.freecad

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")

ADD_A_BLOCK = """
body = doc.getObject('Body')
block = body.newObject('PartDesign::AdditiveBox', 'Block')
block.Length, block.Width, block.Height = 10, 10, 5
block.Placement = App.Placement(App.Vector(0, 0, 10), App.Rotation())
doc.recompute()
"""

A_CUBE = """
import Part
body = doc.addObject('PartDesign::Body', 'Body')
sketch = body.newObject('Sketcher::SketchObject', 'Sketch')
sketch.AttachmentSupport = [(body.Origin.OriginFeatures[3], '')]
sketch.MapMode = 'FlatFace'
for a, b in [((0, 0), (10, 0)), ((10, 0), (10, 10)), ((10, 10), (0, 10)), ((0, 10), (0, 0))]:
    sketch.addGeometry(Part.LineSegment(App.Vector(*a, 0), App.Vector(*b, 0)))
pad = body.newObject('PartDesign::Pad', 'Pad')
pad.Profile = sketch
pad.Length = 10
doc.recompute()
"""


def _run(monkeypatch, replies, *flags):
    import sdk

    llm = ScriptedLLMClient(replies)
    monkeypatch.setattr(sdk, "client_for", lambda *args: llm)
    monkeypatch.setattr(sys, "argv", ["run.py", "--model", "scripted", *flags])
    return run.main()


def _edit_fixture(tmp_path):
    import Part

    folder = tmp_path / "101"
    (folder / "renders").mkdir(parents=True)
    Part.makeBox(40, 30, 10).exportStep(str(folder / "input.step"))
    for view in ("front", "right", "top", "iso"):
        (folder / "renders" / (view + ".png")).write_bytes(PNG)
    (folder / "edit_description.txt").write_text("Add a 10 x 10 x 5 block on top.\n")
    return str(folder)


def test_an_edit_task_leaves_its_numbers_and_the_base_beside_the_saved_step(monkeypatch, tmp_path, capsys):
    folder = _edit_fixture(tmp_path)
    save = str(tmp_path / "out" / "run.step")
    os.makedirs(os.path.dirname(save))

    code = _run(monkeypatch, [say(code=ADD_A_BLOCK), say(finish="a block on top")],
                "--task", folder, "--log", str(tmp_path / "run.jsonl"), "--save", save)

    assert code == 0
    assert "steps applied : 1" in capsys.readouterr().out
    with open(str(tmp_path / "out" / "run.json"), encoding="utf-8") as handle:
        result = json.load(handle)
    assert result["solids"] == 1 and result["valid"] is True
    assert result["volume"] == pytest.approx(12500)
    assert result["base_volume"] == pytest.approx(12000)
    import Part

    assert Part.read(str(tmp_path / "out" / "run.base.step")).Volume == pytest.approx(12000)
    assert sorted(os.listdir(tmp_path / "out")) == ["run.base.step", "run.json", "run.step"]


def test_a_part_run_from_an_intent_alone_leaves_its_numbers_and_no_base(monkeypatch, tmp_path):
    save = str(tmp_path / "run.step")

    code = _run(monkeypatch, [say(code=A_CUBE), say(finish="a 10 mm cube")],
                "--intent", "Make a 10 mm cube.", "--log", str(tmp_path / "run.jsonl"), "--save", save)

    assert code == 0
    with open(str(tmp_path / "run.json"), encoding="utf-8") as handle:
        result = json.load(handle)
    assert result == {"solids": 1, "valid": True, "volume": pytest.approx(1000), "base_volume": None}
    assert not os.path.exists(str(tmp_path / "run.base.step"))
