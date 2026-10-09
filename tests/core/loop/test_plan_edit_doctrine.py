"""
An edit's first Plan prompt carries the edit doctrine and the verb-to-TypeId table the
op_mismatch gate reads, and a run nobody answers is told so and not offered `question`.
"""
import json
import os

from core.events import EventStream
from core.lint import OP_TYPEIDS
from core.llm import ScriptedLLMClient
from core.loop import plan as plan_module
from core.loop import prompt as prompts
from core.loop.prompt import DESIGN_PHASE, prompt
from core.prompts import ROOT

BASE = "The part before the edit, in mm:\nvolume: 10000 mm3, solids: 1"


def _system_of_the_first_plan(monkeypatch, base=BASE):
    seen = []

    def fake_run_phase(kind, working_dir, sources, llm, task, **kwargs):
        seen.append(kwargs["system"])
        return {"question": "which?"}, None, []

    monkeypatch.setattr(plan_module, "run_phase", fake_run_phase)
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


def test_a_run_nobody_answers_says_so_and_does_not_offer_question(monkeypatch):
    with prompts.nobody_answering():
        system = _system_of_the_first_plan(monkeypatch)
        designing = prompt(DESIGN_PHASE)

    for text in (system, designing):
        assert "Nobody answers" in text
        assert "`question`" not in text
        assert '"question"' not in text


def test_an_interactive_run_still_offers_question(monkeypatch):
    system = _system_of_the_first_plan(monkeypatch)

    assert '{"declare": {"question": "..."}}' in system
    assert "Nobody answers" not in system
    assert '"question": "..."' in prompt(DESIGN_PHASE)


def test_run_without_a_person_to_ask_plans_without_question():
    from tests.loop_helpers import _system_messages, drive, plan_reply
    _, _, llm, _ = drive([plan_reply("Pad it 10 mm")], intent="x")
    plans = [text for text in _system_messages(llm) if text.startswith("You plan changes")]

    assert plans and all("Nobody answers" in text for text in plans)


def test_run_with_a_person_to_ask_offers_question():
    from tests.loop_helpers import _system_messages, drive, plan_reply
    _, _, llm, _ = drive([plan_reply("Pad it 10 mm")], intent="x", ask=lambda question: "ten")
    plans = [text for text in _system_messages(llm) if text.startswith("You plan changes")]

    assert plans and all("Nobody answers" not in text for text in plans)
