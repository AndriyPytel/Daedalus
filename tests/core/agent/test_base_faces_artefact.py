"""The full address list of the base part reaches Design and Plan as a read-only `base_faces.json`, because the inventory in the request is cut."""
import json
import os
import stat

from core.agent import phase_contract as pc
from core.loop import design as design_module
from core.loop import plan as plan_module
from core.llm import ScriptedLLMClient
from core.events import EventStream

FACES = {"plane z = 10": 100.0, "cone axis (0, 0, 1), apex (0, 0, -142.768), angle 45": 478.9}


def test_only_design_and_plan_declare_base_faces_and_both_read_only():
    declaring = {phase for phase, row in pc.PHASE_CONTRACT.items() if "base_faces" in row}

    assert declaring == {pc.DESIGN, pc.PLAN}
    assert pc.PHASE_CONTRACT[pc.DESIGN]["base_faces"] == pc.RO == pc.PHASE_CONTRACT[pc.PLAN]["base_faces"]


def test_spawn_writes_the_faces_read_only_for_design_and_plan(tmp_path):
    for phase in (pc.DESIGN, pc.PLAN):
        working = pc.spawn(phase, str(tmp_path / phase), base_faces=FACES)
        path = os.path.join(working, "base_faces.json")
        assert json.load(open(path)) == FACES
        assert not os.stat(path).st_mode & stat.S_IWUSR


def test_spawn_writes_nothing_for_a_phase_that_does_not_declare_them_or_a_run_without_a_base(tmp_path):
    assert "base_faces.json" not in os.listdir(pc.spawn(pc.EXECUTE, str(tmp_path / "e"), base_faces=FACES))
    assert "base_faces.json" not in os.listdir(pc.spawn(pc.DESIGN, str(tmp_path / "d"), intent="x"))


def _sources_of(monkeypatch, module):
    seen = []

    def declaring(phase, sources, *args, **kwargs):
        seen.append(dict(sources))
        return {}, None

    monkeypatch.setattr(module, "run_declaring", declaring)
    return seen


def test_design_hands_the_run_s_faces_to_its_phase_and_a_generation_run_hands_none(monkeypatch):
    seen = _sources_of(monkeypatch, design_module)

    with pc.the_base_faces(FACES):
        design_module.design({"intent": "edit"}, ScriptedLLMClient([]), EventStream())
    design_module.design({"intent": "a box"}, ScriptedLLMClient([]), EventStream())

    assert seen[0]["base_faces"] == FACES and "base_faces" not in seen[1]


def test_plan_hands_the_run_s_faces_to_its_phase(monkeypatch):
    seen = _sources_of(monkeypatch, plan_module)

    with pc.the_base_faces(FACES):
        try:
            plan_module.plan("edit", {}, ScriptedLLMClient([]), EventStream(), base="x")
        except Exception:
            pass

    assert seen[0] == {"base_faces": FACES}


def test_the_run_reads_the_faces_off_the_tools_once_a_base_part_exists():
    from tests.loop_helpers import FakeTools

    tools = FakeTools(base="planes:\n  plane z = 10 (area 100 mm2)", faces=[{"base": FACES, "now": []}])

    with pc.the_base_faces_of(tools.registry):
        assert pc.BASE_FACES.get() == FACES
    assert pc.BASE_FACES.get() is None
    with pc.the_base_faces_of(FakeTools().registry):
        assert pc.BASE_FACES.get() is None
