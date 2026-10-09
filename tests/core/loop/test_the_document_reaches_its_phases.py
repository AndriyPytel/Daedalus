"""
Every phase whose `PHASE_CONTRACT` row declares a Document is handed
one — Execute opens what the Plan was written against, Assertion measures a
copy of it — and the kernel's answer to a code turn reaches the session log.
"""
import json
import os

import pytest

from core.agent import agent as agent_module
from core.agent import phase_contract as pc
from core.agent import run_phase
from core.events import TOOL_RESULT, EventStream
from core.kernel import Kernel, KernelResult, copy_of, find_freecadcmd
from core.llm import ScriptedLLMClient
from core.loop.assertion import assertion
from core.loop.execute import execute
from core.tools import ToolRegistry
from tests.event_helpers import events_of


def test_execute_opens_the_document_the_snapshot_describes(monkeypatch, tmp_path):
    """
    The brief carries a Snapshot of the live Document while the kernel got a
    brand-new empty one, so every Step was written against objects that were
    not there. A recorded run
    (`logs/sessions/a478f2fe6315476f85b370427e3724fb.jsonl`) spent seven turns
    proving the Document was empty and then voided the Plan as obsolete.
    """
    copy = tmp_path / "live.FCStd"
    copy.write_text("not really a Document, only a path to carry")
    registry = ToolRegistry()
    registry.tool("snapshot", "State right now.")(lambda: {"objects": [], "object_count": 0})
    registry.tool("document_copy", "The Document for a phase kernel.")(
        lambda: {"path": str(copy)})
    seen = {}

    def fake_run_phase(*args, **kwargs):
        seen.update(kwargs)
        return {"steps": [{"step": 1, "code": "pass", "label": "one"}]}, None, []

    monkeypatch.setattr(agent_module, "run_phase", fake_run_phase)

    execute(["pad a box"], registry, ScriptedLLMClient([]), None)

    assert seen["document"] == str(copy)
    assert not copy.exists(), "the copy is the caller's to delete"


def test_the_working_directory_is_gone_once_execute_returns(monkeypatch, tmp_path):
    """
    `working_dir` holds the kernel's saved copy of every declared Step —
    kept only as long as `execute` is reading `declarations` out of it, not
    for the life of the process.
    """
    registry = ToolRegistry()
    registry.tool("snapshot", "State right now.")(lambda: {"objects": [], "object_count": 0})
    registry.tool("document_copy", "The Document for a phase kernel.")(
        lambda: {"path": str(tmp_path / "live.FCStd")})
    seen = {}

    def fake_run_phase(kind, working_dir, *args, **kwargs):
        seen["working_dir"] = working_dir
        return ({"steps": [{"step": 1, "label": "one"}]}, None,
                [{"step": 1, "code": "pass", "label": "one", "checkpoint": ""}])

    monkeypatch.setattr(agent_module, "run_phase", fake_run_phase)

    execute(["pad a box"], registry, ScriptedLLMClient([]), None)

    assert not os.path.exists(seen["working_dir"])


def test_the_working_directory_is_gone_even_when_execute_raises(monkeypatch, tmp_path):
    """A phase that blew up still leaves nothing of its own workspace behind."""
    registry = ToolRegistry()
    registry.tool("snapshot", "State right now.")(lambda: {"objects": [], "object_count": 0})
    registry.tool("document_copy", "The Document for a phase kernel.")(
        lambda: {"path": str(tmp_path / "live.FCStd")})
    seen = {}

    def fake_run_phase(kind, working_dir, *args, **kwargs):
        seen["working_dir"] = working_dir
        raise RuntimeError("the phase died")

    monkeypatch.setattr(agent_module, "run_phase", fake_run_phase)

    with pytest.raises(RuntimeError):
        execute(["pad a box"], registry, ScriptedLLMClient([]), None)

    assert not os.path.exists(seen["working_dir"])


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


@pytest.mark.freecad
def test_the_first_code_turn_opens_the_document_it_is_given(kernel, tmp_path):
    import FreeCAD

    document = FreeCAD.newDocument("Given")
    document.addObject("Part::Box", "Box")
    document.recompute()
    path = copy_of(document)
    FreeCAD.closeDocument(document.Name)

    llm = ScriptedLLMClient([
        json.dumps({"code": "print([o.Name for o in doc.Objects])"}),
        json.dumps({"declare": {"saw": "it"}}),
    ])

    _, session, _ = run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm,
                              "look at the Document", document=path, kernel=kernel)

    assert "Box" in json.dumps(llm.calls[-1][-1], default=str)
    os.remove(path)


@pytest.mark.freecad
def test_a_phase_that_names_no_document_keeps_the_kernel_session(kernel, tmp_path):
    """`None` is the kernel's own word for an empty Document, so a phase that
    passes nothing must not reach the kernel as one."""
    kernel.check("doc.addObject('Part::Box', 'Kept'); doc.recompute()")

    llm = ScriptedLLMClient([
        json.dumps({"code": "print([o.Name for o in doc.Objects])"}),
        json.dumps({"declare": {"saw": "it"}}),
    ])

    run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "look", kernel=kernel)

    assert "Kept" in json.dumps(llm.calls[-1][-1], default=str)


def test_a_code_turn_puts_the_kernel_answer_on_the_stream(tmp_path):
    """
    The session log carried the code the model wrote and nothing the kernel
    said back, so a run that failed every Step could not be read at all.
    """
    class OneFailingKernel:
        def check(self, code, **kwargs):
            return KernelResult(ok=False, traceback="RuntimeError: no such property")

    llm = ScriptedLLMClient([
        json.dumps({"code": "doc.addObject('Part::Box', 'Box').Nope = 1"}),
        json.dumps({"declare": {"gave": "up"}}),
    ])
    stream = EventStream()

    run_phase(pc.EXECUTE, str(tmp_path / "execute"), {}, llm, "pad a box",
              kernel=OneFailingKernel(), stream=stream)

    answered = events_of(stream, TOOL_RESULT)
    assert [event.data["result"]["status"] for event in answered] == ["failed"]
    assert "no such property" in answered[0].data["result"]["traceback"]


def test_only_the_phases_whose_row_declares_a_document_are_handed_one(tmp_path):
    """
    `PHASE_CONTRACT` is what says who sees the Document — Execute builds on it
    and Assertion measures it, Design, Plan and Review are denied it — so the
    same call hands Execute a `document.FCStd` and Review nothing.
    """
    document = tmp_path / "live.FCStd"
    document.write_bytes(b"not a real Document, only bytes to copy")
    landed = {}

    for phase in (pc.EXECUTE, pc.REVIEW):
        working = tmp_path / phase
        llm = ScriptedLLMClient([json.dumps({"declare": {"verdict": "pass"}})])
        run_phase(phase, str(working), {}, llm, "judge it", document=str(document))
        landed[phase] = os.listdir(working)

    assert "document.FCStd" in landed[pc.EXECUTE]
    assert "document.FCStd" not in landed[pc.REVIEW]


def test_assertion_measures_against_a_copy_of_the_document(monkeypatch, tmp_path):
    """Assertion's row is `document: RO`, and its prompt promises a throwaway
    copy of the Document to measure — it was given an empty kernel."""
    copy = tmp_path / "live.FCStd"
    copy.write_text("not really a Document, only a path to carry")
    registry = ToolRegistry()
    registry.tool("document_copy", "The Document for a phase kernel.")(
        lambda: {"path": str(copy)})
    registry.tool("check_assertions", "Measure the live Document.", {
        "type": "object", "properties": {"code": {"type": "string"}},
    })(lambda code: {"ok": True, "held": [], "geometry": {}})
    seen = {}

    def fake_run_phase(*args, **kwargs):
        seen.update(kwargs)
        return {"code": "assert True"}, None, []

    monkeypatch.setattr(agent_module, "run_phase", fake_run_phase)

    assertion(["a bracket 10 mm thick"], {}, registry, ScriptedLLMClient([]), EventStream())

    assert seen["document"] == str(copy)
    assert not copy.exists(), "the copy is the caller's to delete"


@pytest.mark.freecad
def test_a_declared_step_with_no_code_in_the_final_reply_still_reaches_apply_step(kernel, tmp_path):
    """
    The regression that shipped: `execute.md` tells the model to
    end the phase with `{"declare": {"steps": [{"step": 1, "label": "..."}]}}`
    -- no `code` -- because the code already lives in the kernel's
    declarations. `_written` used to read only the reply's own `steps`, so an
    obedient model produced an empty `Written` even though the kernel had
    already run and checkpointed the Step, and it never reached `apply_step`.
    """
    from core.loop.budgets import Budgets
    from tests.loop_helpers import drive, plan_reply

    result, events, llm, tools = drive([
        plan_reply("Add a box"),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box')",
                    "step": 1, "label": "Add the box"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "Add the box"}]}}),
    ], budgets=Budgets(attempts_per_step=1, trailing_gap_retries=0, revisions=0),
        intent="Add a box")

    assert tools.applied_codes() == ["doc.addObject('Part::Box', 'Box')"], (
        "the declared Step never reached apply_step: %s" % result.reason)
