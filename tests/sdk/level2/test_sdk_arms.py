"""
Level 2 -- the two comparison arms of `sdk.Client`, `agent` and `oneshot`,
against a real headless kernel and a real FreeCAD: one model choosing its own
route over the kernel, and one model call executed once.
"""
import json
import os

import pytest

from core.events import (APPLIED, BUDGET_EXHAUSTED, COMPLETED, COST, DESIGN, FAILED, GATE,
                         PLAN, PREFLIGHT_FAILED, REVIEW_VERDICT)
from core.kernel import Kernel, find_freecadcmd
from core.llm import ScriptedLLMClient

pytestmark = pytest.mark.freecad

BOX = ("b = doc.addObject('Part::Box', 'Box')\nb.Length = 10\nb.Width = 10\nb.Height = 10\n"
       "doc.recompute()")


def _say(**turn):
    return json.dumps(turn)


@pytest.fixture(scope="module")
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel(timeout=120) as running:
        yield running


def _client(llm, kernel):
    from sdk import Client

    client = Client(kernel=kernel, llm=llm)
    seen = []
    client.watch(seen.append)
    client.seen = seen
    return client


def _kinds(client):
    return [event.kind for event in client.seen]


def _volume_of(step_path):
    import Part

    return Part.read(step_path).Volume


def _user_text(call):
    content = call[-1]["content"]
    return content if isinstance(content, str) else json.dumps(content)


def test_the_agent_picks_its_own_route_and_the_step_it_built_is_exported(kernel, tmp_path):
    llm = ScriptedLLMClient([
        _say(probe="print(len(doc.Objects))"),
        _say(code="raise ValueError('nope')"),
        _say(code=BOX),
        _say(inspect={"tool": "describe_object", "arguments": {"name": "Box"}}),
        _say(render=True),
        _say(finish="a 10 mm cube"),
    ])
    client = _client(llm, kernel)

    result = client.agent("Make a 10 mm cube")

    assert result.ok, result.reason
    assert result.steps_applied == 1
    kinds = _kinds(client)
    assert kinds.count(COST) == 6
    assert kinds.count(APPLIED) == 1
    assert kinds.count(PREFLIGHT_FAILED) == 1
    assert COMPLETED in kinds
    assert not {DESIGN, PLAN, GATE, REVIEW_VERDICT} & set(kinds)
    assert "nope" in _user_text(llm.calls[2])
    assert '"volume": 1000' in _user_text(llm.calls[4])
    assert any(block.get("type") == "image_url" for block in llm.calls[5][-1]["content"])

    saved = client.export_step(str(tmp_path / "out.step"))
    assert _volume_of(saved) == pytest.approx(1000)
    client.close()


def test_an_agent_out_of_turns_is_stopped_and_keeps_what_it_built(kernel, tmp_path):
    llm = ScriptedLLMClient([_say(code=BOX), _say(probe="print(1)"), _say(probe="print(2)")])
    client = _client(llm, kernel)

    result = client.agent("Make a 10 mm cube", max_turns=2)

    assert result.ok and result.steps_applied == 1
    assert BUDGET_EXHAUSTED in _kinds(client)
    assert len(llm.calls) == 2
    assert _volume_of(client.export_step(str(tmp_path / "out.step"))) == pytest.approx(1000)
    client.close()


def test_an_agent_is_stopped_by_the_budget_ceiling(kernel):
    llm = ScriptedLLMClient([_say(code=BOX), _say(probe="print(1)")])
    client = _client(llm, kernel)

    result = client.agent("Make a cube", cancelled=lambda: len(llm.calls) >= 1)

    assert len(llm.calls) == 1
    assert BUDGET_EXHAUSTED in _kinds(client)
    assert result.steps_applied == 1
    client.close()


def test_a_crash_in_the_agents_code_does_not_end_the_run(kernel):
    llm = ScriptedLLMClient([
        _say(code=BOX),
        _say(code="import os\nos._exit(1)"),
        _say(code=BOX.replace("'Box'", "'Second'")),
        _say(finish="done"),
    ])
    client = _client(llm, kernel)

    result = client.agent("Make a cube")

    assert "kernel died" in _user_text(llm.calls[2])
    assert result.ok and result.steps_applied == 2
    client.close()


def test_a_reply_that_is_no_tool_call_is_asked_again(kernel):
    llm = ScriptedLLMClient(["I think a cube.", _say(code=BOX), _say(finish="done")])
    client = _client(llm, kernel)

    result = client.agent("Make a cube")

    assert "JSON" in _user_text(llm.calls[1])
    assert result.ok
    client.close()


def test_the_agent_that_built_nothing_has_nothing_to_export(kernel):
    llm = ScriptedLLMClient([_say(finish="I give up")])
    client = _client(llm, kernel)

    result = client.agent("Make a cube")

    assert not result.ok and result.steps_applied == 0
    client.close()


def test_oneshot_is_one_call_executed_once_and_exported(tmp_path):
    from sdk import NO_KERNEL

    llm = ScriptedLLMClient(["Here you go:\n```python\n%s\n```" % BOX])
    from sdk import Client

    client = Client(kernel=NO_KERNEL, llm=llm)
    seen = []
    client.watch(seen.append)

    result = client.oneshot("Make a 10 mm cube")

    assert result.ok, result.reason
    assert len(llm.calls) == 1
    kinds = [event.kind for event in seen]
    assert kinds.count(COST) == 1 and kinds.count(APPLIED) == 1 and COMPLETED in kinds
    assert _volume_of(client.export_step(str(tmp_path / "out.step"))) == pytest.approx(1000)
    client.close()


def test_oneshot_does_not_retry_a_script_that_raises_and_keeps_the_geometry_it_made():
    from sdk import NO_KERNEL, Client

    llm = ScriptedLLMClient(["```python\n%s\nmissing_name()\n```" % BOX])
    client = Client(kernel=NO_KERNEL, llm=llm)
    seen = []
    client.watch(seen.append)

    result = client.oneshot("Make a cube")

    assert not result.ok
    assert "missing_name" in result.reason
    assert len(llm.calls) == 1
    kinds = [event.kind for event in seen]
    assert PREFLIGHT_FAILED in kinds and FAILED in kinds
    assert [o.Name for o in client.document.Objects] == ["Box"]
    client.close()


def _a_box_pipeline(code_turns):
    return ScriptedLLMClient(["design",
                              json.dumps({"declare": {"design": {}, "question": ""}}),
                              json.dumps({"declare": {"steps": ["Add a 10mm cube"]}})]
                             + code_turns
                             + [json.dumps({"declare": {"steps": [{"step": 1, "label": "box"}]}}),
                                json.dumps({"declare": {"suspected": []}}),
                                json.dumps({"declare": {"verdict": "pass", "reason": "a cube"}})])


class _WatchedKernel(Kernel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.building = []

    def check(self, code, **kwargs):
        if kwargs.get("builds"):
            self.building.append(code)
        return super().check(code, **kwargs)


def test_nopreflight_lands_a_step_through_apply_alone_and_the_pipeline_still_finishes(tmp_path):
    from sdk import Client
    from core.loop.budgets import Budgets

    llm = _a_box_pipeline([_say(code="doc.addObject('Part::Box', 'Box')", step=1, label="box")])
    with _WatchedKernel(timeout=120) as watched:
        client = Client(kernel=watched, llm=llm)
        seen = []
        client.watch(seen.append)

        result = client.send("Add a 10mm cube", budgets=Budgets(preflight=False))

        assert result.ok, result.reason
        assert watched.building == []
        assert [o.Name for o in client.document.Objects] == ["Box"]
        assert REVIEW_VERDICT in [event.kind for event in seen]
        client.close()


def test_nopreflight_hands_an_apply_failure_back_like_any_other(tmp_path):
    from sdk import Client
    from core.loop.budgets import Budgets

    llm = _a_box_pipeline([
        _say(code="missing_name()", step=1, label="bad"),
        _say(code="doc.addObject('Part::Box', 'Box')", step=1, label="box")])
    with _WatchedKernel(timeout=120) as watched:
        client = Client(kernel=watched, llm=llm)
        seen = []
        client.watch(seen.append)

        result = client.send("Add a 10mm cube", budgets=Budgets(preflight=False))

        assert result.ok, result.reason
        failed = [e for e in seen if e.kind == PREFLIGHT_FAILED]
        assert [e.data["gate"] for e in failed] == ["rollback"]
        assert "missing_name" in failed[0].data["traceback"]
        client.close()


TABLE = {"views": [{"crop": 1, "view": "front"}], "overall": {"x": 10, "y": 10, "z": 10},
         "through_count": 0, "thin_walled": False, "body_family": "prismatic"}


def _sheet(tmp_path):
    from PIL import Image, ImageDraw

    path = str(tmp_path / "input.png")
    picture = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(picture)
    draw.rectangle((100, 100, 500, 400), outline="black", width=4)
    draw.rectangle((650, 100, 1000, 300), outline="black", width=4)
    picture.save(path)
    return path


def _reader(llm, kernel, sheet):
    client = _client(llm, kernel)
    result = client.agent("Make the part", attachments=[sheet], reader=True)
    return client, result


def test_the_reader_arm_reads_the_drawing_once_then_builds_with_the_table_in_front_of_it(kernel, tmp_path):
    llm = ScriptedLLMClient([_say(**TABLE), _say(code=BOX), _say(finish="a cube"), _say(finish="a cube")])

    client, result = _reader(llm, kernel, _sheet(tmp_path))

    assert result.ok and len(llm.calls) == 4
    opening = json.dumps(llm.calls[1][-1]["content"])
    assert "A first reading of the sheet" in opening and "Crop 1" in opening
    assert opening.count("image_url") >= 2
    client.close()


def test_the_finish_gate_returns_the_differences_only_and_a_second_finish_is_accepted(kernel, tmp_path):
    table = dict(TABLE, thin_walled=True)
    llm = ScriptedLLMClient([_say(**table), _say(code=BOX), _say(finish="a cube"),
                             _say(finish="still a cube")])

    client, result = _reader(llm, kernel, _sheet(tmp_path))

    assert result.ok and len(llm.calls) == 4
    sent = _user_text(llm.calls[3])
    assert "thin-walled" in sent and "conform" not in sent.lower()
    assert "front view, then crop 1" in sent
    client.close()


def test_a_part_that_matches_the_table_is_still_answered_once_with_its_views_and_then_let_go(kernel, tmp_path):
    table = dict(TABLE, views=[{"crop": 1, "view": "section A-A, right"}])
    llm = ScriptedLLMClient([_say(**table), _say(code=BOX), _say(finish="a cube"), _say(finish="a cube")])

    client, result = _reader(llm, kernel, _sheet(tmp_path))

    assert result.ok and len(llm.calls) == 4
    sent = _user_text(llm.calls[3])
    assert "right view, then crop 1 (section A-A, right)" in sent
    assert "Name each feature of the drawing the part lacks or gets wrong" in sent
    assert "What was measured" not in sent
    assert "goes through only where a section or hidden lines show it go through" in sent
    logged = next(event.data["result"] for event in client.seen
                  if event.kind == "tool_result" and event.data.get("name") == "finish_gate")
    assert logged["views"] == ["right"] and "Name each feature of the drawing" in logged["text"]
    client.close()


def test_a_part_that_gained_through_openings_after_the_check_is_told_once_to_confirm_them(kernel, tmp_path):
    drill = ("c = doc.addObject('Part::Cylinder', 'Hole')\nc.Radius = 2\nc.Height = 30\n"
             "c.Placement.Base = App.Vector(5, 5, -10)\ncut = doc.addObject('Part::Cut', 'Cut')\n"
             "cut.Base = doc.getObject('Box')\ncut.Tool = c\ndoc.recompute()")
    llm = ScriptedLLMClient([_say(**TABLE), _say(code=BOX), _say(finish="a cube"), _say(code=drill),
                             _say(finish="drilled"), _say(finish="drilled")])

    client, result = _reader(llm, kernel, _sheet(tmp_path))

    assert result.ok and len(llm.calls) == 6
    sent = _user_text(llm.calls[5])
    assert "added 1 through openings; confirm each one in a section or hidden lines" in sent
    assert "added" not in _user_text(llm.calls[4])
    client.close()


def test_a_failed_reading_is_retried_once_and_the_build_goes_on_without_a_table(kernel, tmp_path):
    llm = ScriptedLLMClient(["no idea", "still no idea", _say(code=BOX), _say(finish="a cube")])

    client, result = _reader(llm, kernel, _sheet(tmp_path))

    assert result.ok and len(llm.calls) == 4
    assert "A first reading" not in json.dumps(llm.calls[2][-1]["content"])
    client.close()


def test_an_edit_that_ships_the_part_unchanged_is_told_once_and_then_let_go(kernel, tmp_path):
    import sys

    import Part

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src", "tools"))
    import run

    step = str(tmp_path / "input.step")
    Part.makeBox(40, 30, 10).exportStep(step)
    seed = run.base_document(step)
    from sdk import Client

    llm = ScriptedLLMClient([_say(finish="nothing to do"), _say(finish="still nothing")])
    client = Client(document=seed, kernel=kernel, llm=llm)
    client.kernel.check("pass", document=seed)

    result = client.agent("Drill a hole", reader=True, base=True)

    assert len(llm.calls) == 2 and "not been made" in _user_text(llm.calls[1])
    out = client.export_step(str(tmp_path / "out.step"), "Body")
    assert _volume_of(out) == pytest.approx(12000)
    client.close()


def test_a_view_render_is_drawn_from_the_direction_it_names(tmp_path):
    from PIL import Image
    import numpy as np

    code = BOX.replace("Width = 10", "Width = 20").replace("Height = 10", "Height = 30")
    sizes = {}
    with Kernel(timeout=120) as fresh:
        for view in ("top", "front", "right"):
            path = str(tmp_path / (view + ".png"))
            assert fresh.check(code, render_path=path, render_view=view).ok
            drawn = np.asarray(Image.open(path).convert("L")) < 200
            rows, columns = np.where(drawn)
            sizes[view] = (columns.max() - columns.min() + 1) / (rows.max() - rows.min() + 1)

    assert sizes["top"] == pytest.approx(10 / 20, rel=0.08)
    assert sizes["front"] == pytest.approx(10 / 30, rel=0.08)
    assert sizes["right"] == pytest.approx(20 / 30, rel=0.08)
