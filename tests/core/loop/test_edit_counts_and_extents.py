"""
The count-and-extent rule reaches the Plan and Review prompts of an edit run, and only of an edit run.
"""
from core.agent import agent as agent_module
from core.events import EventStream
from core.llm import ScriptedLLMClient
from core.loop import plan as plan_module
from core.loop import review as review_module

BASE = "The part before the edit, in mm:\nvolume: 10000 mm3, solids: 1"


def _plan_system(monkeypatch, base):
    seen = []

    def fake_run_phase(kind, working_dir, sources, llm, task, **kwargs):
        seen.append(kwargs["system"])
        return {"question": "which?"}, None, []

    monkeypatch.setattr(agent_module, "run_phase", fake_run_phase)
    plan_module.plan("edit", {}, ScriptedLLMClient([]), EventStream(), base=base)
    return " ".join(seen[0].split())


def _review_system(monkeypatch, base):
    seen = []

    def fake_run_declaring(contract, check, llm, content, **kwargs):
        seen.append(kwargs["system"])
        return {"verdict": "pass", "reason": ""}, None

    monkeypatch.setattr(review_module, "run_declaring", fake_run_declaring)
    review_module.review("edit", (), "described", ScriptedLLMClient([]), EventStream(), base=base)
    return " ".join(seen[0].split())


def test_the_plan_of_an_edit_lists_every_affected_feature_as_a_count_and_an_extent(monkeypatch):
    system = _plan_system(monkeypatch, BASE)

    assert "lists each feature the Intent affects as a count and an extent" in system
    assert "all 7 blades, not the hub" in system
    assert "`base_faces`" in system


def test_the_plan_of_an_edit_writes_no_step_beyond_that_list(monkeypatch):
    system = _plan_system(monkeypatch, BASE)

    assert "a Step beyond it" in system
    assert "already satisfied" in system
    assert "with the measurement" in system


def test_the_review_of_an_edit_recounts_the_intents_counts_after_the_last_step(monkeypatch):
    system = _review_system(monkeypatch, BASE)

    assert "re-count" in system
    assert "a feature the Intent did not name that changed" in system


def test_a_run_with_no_base_part_gets_none_of_the_rule(monkeypatch):
    plan_system = _plan_system(monkeypatch, None)
    review_system = _review_system(monkeypatch, None)

    assert "count and an extent" not in plan_system
    assert "a Step beyond it" not in plan_system
    assert "re-count" not in review_system
    assert "did not name that changed" not in review_system
