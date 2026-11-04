"""
Level 1 -- the Assembly Domain's `build_part` call hands a sub-agent its name, intent and drawings, and answers the parent with the summary alone.
"""
import json

from core.agent.assembly import run_assembly
from core.events import EventStream
from core.llm import ScriptedLLMClient


def _run(turns, build_part):
    llm = ScriptedLLMClient([json.dumps(turn) for turn in turns + [turns[0]]])
    asked = iter([False, False, True])
    result = run_assembly("Build two plates.", llm=llm, kernel=None, tools=None, events=EventStream(),
                          attachments=["/d/assembly.png"], cancelled=lambda: next(asked, True),
                          build_part=build_part)
    return llm, result


def test_the_sub_agent_is_handed_the_name_the_intent_and_the_drawings_the_parent_forwarded():
    seen = []

    def build_part(name, intent, attachments):
        seen.append((name, intent, attachments))
        return {"ok": True, "name": "Plate", "path": "/p/Plate.FCStd", "volume": 500.0, "faces": 6}

    _run([{"build_part": {"name": "Plate", "intent": "A 10x10x5 plate.", "attachments": ["/d/assembly.png"]}}],
         build_part)

    assert seen[0] == ("Plate", "A 10x10x5 plate.", ["/d/assembly.png"])


def test_the_parent_gets_the_summary_the_sub_agent_returned_and_nothing_else():
    summary = {"ok": True, "name": "Plate", "path": "/p/Plate.FCStd", "bbox": [0, 0, 0, 10, 10, 5], "volume": 500.0,
               "faces": 6, "usd": 0.42}

    llm, _ = _run([{"build_part": {"name": "Plate", "intent": "A plate."}}], lambda *a: summary)

    answered = llm.calls[1][-1]["content"]
    assert json.loads(answered) == summary


def test_a_call_with_no_intent_is_answered_with_what_is_missing_and_builds_nothing():
    seen = []

    llm, _ = _run([{"build_part": {"name": "Plate"}}], lambda *a: seen.append(a))

    assert seen == []
    assert "intent" in json.loads(llm.calls[1][-1]["content"])["error"]


def test_a_domain_without_calls_has_none():
    from core.agent.domain import Domain

    assert Domain("part", "", [], lambda: None, lambda kernel: None).calls == {}
