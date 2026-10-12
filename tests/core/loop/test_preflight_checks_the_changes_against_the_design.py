"""
Preflight on an edit: a Step's `changes` must be a subset of the addresses of the features Design marks `changed`, and a Step that asks for more is handed back before anything is built.
"""
from core.events import PREFLIGHT_FAILED, EventStream
from tests.event_helpers import events_of
from core.loop.execute import execute
from core.llm import ScriptedLLMClient
from tests.loop_helpers import FakeTools, StubKernel, code_reply

DESIGN = {"features": [
    {"id": "flange", "edit": "changed", "at": ["plane z = 10"]},
    {"id": "plane z = 0", "edit": "kept"},
    {"id": "bore", "edit": "kept"}]}


def _executed(changes, design=DESIGN):
    tools = FakeTools(base="plane z = 10")
    events = EventStream()
    written = execute(["Pad the flange"], tools.registry, ScriptedLLMClient(code_reply("pad()")), events,
                      kernel=StubKernel(), base="plane z = 10", design=design, changes=[changes])
    return written, tools, events


def test_a_step_naming_only_changed_addresses_is_built():
    written, tools, _ = _executed(["plane z = 10"])

    assert tools.applied_codes() == ["pad()"]
    assert written.for_step(1).apply_failed is None


def test_a_step_naming_an_address_the_intent_did_not_ask_to_change_fails_before_the_build():
    written, tools, events = _executed(
        ["plane z = 10", "hole diameter 30, axis (0, 0, 1), at (0, 0, 0)"])

    assert tools.applied_codes() == []
    failed = events_of(events, PREFLIGHT_FAILED)
    assert [event.data["gate"] for event in failed] == ["changes_unasked"]
    said = written.for_step(1).apply_failed
    assert "did not ask" in said and said.count("hole diameter 30, axis (0, 0, 1), at (0, 0, 0)") == 1


def test_a_step_that_names_nothing_passes_preflight():
    written, tools, _ = _executed([])

    assert tools.applied_codes() == ["pad()"] and written.for_step(1).apply_failed is None


def test_a_run_without_a_base_part_is_not_checked():
    tools = FakeTools()
    written = execute(["Pad"], tools.registry, ScriptedLLMClient(code_reply("pad()")), EventStream(),
                      kernel=StubKernel(), changes=[["plane z = 99"]])

    assert tools.applied_codes() == ["pad()"] and written.for_step(1).apply_failed is None
