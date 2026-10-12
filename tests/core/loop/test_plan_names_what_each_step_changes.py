"""On an edit each Step names the base-part addresses it changes, in a field `changes` the Plan carries beside `ops`."""
from core.agent import agent as agent_module
from core.events import EventStream
from core.llm import ScriptedLLMClient
from core.loop import plan as plan_module
from core.loop.prompt import prompt

BASE = "The part before the edit, in mm:\nvolume: 10000 mm3, solids: 1"


def test_steps_of_collects_each_kept_step_s_changes_in_step_order():
    changes = []

    steps, *_ = plan_module._steps_of(
        [{"do": "pad the flange 5 mm", "changes": ["plane z = 10", " plane  z = 0 "]},
         {"do": ""},
         {"do": "fillet the rim"},
         "a bare step"],
        changes=changes)

    assert len(steps) == 3
    assert changes == [["plane z = 10", "plane z = 0"], [], []]


def test_a_plan_carries_the_changes_of_its_steps(monkeypatch):
    declared = {"steps": [{"do": "pad the flange 5 mm", "changes": ["plane z = 10"]}]}
    monkeypatch.setattr(agent_module, "run_phase",
                        lambda *args, **kwargs: (dict(declared), None, []))

    planned = plan_module.plan("edit", {}, ScriptedLLMClient([]), EventStream(), base=BASE)

    assert planned.changes == [["plane z = 10"]]


def test_the_plan_prompt_of_an_edit_explains_changes():
    said = " ".join(prompt("plan").split())

    assert '"changes"' in said and "the_base_part" in said
