"""
An edit's first Plan prompt carries the edit doctrine and the verb-to-TypeId table the
op_mismatch gate reads, and a run nobody answers is told so, while Plan keeps `question` and Design loses it.
"""
import json
import os

from core.agent import agent as agent_module
from core.events import EventStream
from core.lint import OP_TYPEIDS
from core.llm import ScriptedLLMClient
from core.loop import plan as plan_module
from core.loop.prompt import DESIGN_PHASE, prompt
from core.loop.gate import nobody_to_ask
from core.nobody import nobody_answering
from core.prompts import ROOT

BASE = "The part before the edit, in mm:\nvolume: 10000 mm3, solids: 1"


def _system_of_the_first_plan(monkeypatch, base=BASE):
    seen = []

    def fake_run_phase(kind, working_dir, sources, llm, task, **kwargs):
        seen.append(kwargs["system"])
        return {"question": "which?"}, None, []

    monkeypatch.setattr(agent_module, "run_phase", fake_run_phase)
    plan_module.plan("edit", {}, ScriptedLLMClient([]), EventStream(), base=base)
    return seen[0]


def test_the_first_plan_prompt_of_an_edit_lists_every_verb_with_the_typeids_the_gate_accepts(monkeypatch):
    system = _system_of_the_first_plan(monkeypatch)

    for op, typeids in OP_TYPEIDS.items():
        assert "- %s: " % op in system
        for typeid in typeids:
            assert typeid in system


def test_the_table_has_one_source(monkeypatch):
    copies = [name for dirpath, _, names in os.walk(ROOT) for name in names
              if "PartDesign::AdditiveBox" in open(os.path.join(dirpath, name), encoding="utf-8").read()]

    assert copies == []


def test_the_first_plan_prompt_of_an_edit_steers_to_features_on_the_base_part(monkeypatch):
    system = _system_of_the_first_plan(monkeypatch)

    assert "from an existing face" in system
    assert "`sketch_at`" in system
    for standalone in ("Part::Box", "Part::Feature", "tool Body"):
        assert standalone in system
    assert "give the reason" in system


def test_a_run_nobody_answers_says_so_and_design_is_not_offered_question(monkeypatch):
    with nobody_answering():
        designing = prompt(DESIGN_PHASE)

    assert "Nobody answers" in designing
    assert "`question`" not in designing
    assert '"question"' not in designing


def test_a_run_nobody_answers_lets_plan_ask_only_where_no_step_can_be_written(monkeypatch):
    with nobody_answering():
        system = " ".join(_system_of_the_first_plan(monkeypatch).split())

    assert "Nobody answers" in system
    assert '{"declare": {"question": "..."}}' in system
    assert "reply with a `question` instead of `steps`" in system
    assert "Ask a `question` only when no Step can be written at all" in system
    assert "do not work the whole part out before writing the first Step" in system
    assert "Your question does not reach the user" not in system
    assert "say so in `question`, naming both entries" not in system


def test_a_run_nobody_answers_plans_with_a_flagged_assumption_only_as_the_last_resort(monkeypatch):
    with nobody_answering():
        system = " ".join(_system_of_the_first_plan(monkeypatch).split())

    assert "When `nobody_answered` tells you to decide, pick the value yourself, flag it `assumed`" in system
    assert "bind it in your kernel under a name of its own (hump_length = 200.0)" in system
    assert "list that name in the Step's \"values\"" in system
    assert "say in the Step's prose that you assumed it and from what" in system


def test_an_interactive_run_still_offers_question(monkeypatch):
    system = _system_of_the_first_plan(monkeypatch)

    assert '{"declare": {"question": "..."}}' in system
    assert "Nobody answers" not in system
    assert '"question": "..."' in prompt(DESIGN_PHASE)


def test_run_without_a_person_to_ask_plans_without_question():
    from tests.loop_helpers import _system_messages, drive, plan_reply
    _, _, llm, _ = drive([plan_reply("Pad it 10 mm")], intent="x", ask=nobody_to_ask)
    plans = [text for text in _system_messages(llm) if text.startswith("You plan changes")]

    assert plans and all("Nobody answers" in text for text in plans)


def test_run_with_a_person_to_ask_offers_question():
    from tests.loop_helpers import _system_messages, drive, plan_reply
    _, _, llm, _ = drive([plan_reply("Pad it 10 mm")], intent="x", ask=lambda question: "ten")
    plans = [text for text in _system_messages(llm) if text.startswith("You plan changes")]

    assert plans and all("Nobody answers" not in text for text in plans)
