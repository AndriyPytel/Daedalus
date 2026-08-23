"""
Execute as an RLM-agent, without the Execute->Plan consult (FCC-187).

`core/loop.py :: execute()` already ran a bounded loop over the read-only
Tools before this ticket, one `_ask` call per look. What changes here is the
seam FCC-183 built and left unwired at Execute: `agentic=True` sends the
whole turn -- every look and every `run` action -- through `core/agent.py ::
ask` instead, the same way `plan(agentic=True)` already does. The
"do exactly this / hand back" contract (ADR-0008) and the hand-back to Plan
on Step failure (ADR-0013) are unchanged either way; `_work_through`'s own
`attempts_per_step` retry loop does not know which path wrote the code.

No FreeCAD anywhere in this file: `FakeTools` (`tests/test_loop.py`) stands
in for the Document, and `ScriptedLLMClient` (`core/llm.py`) stands in for
the provider. Execute->Plan consults (FCC-188), trap proposals, and every
other phase are out of scope.
"""
import json

from core import loop
from core.events import APPLIED, PLAN_REVISED, PREFLIGHT_FAILED, STEP_STARTED, TOOL_CALL
from core.loop import Budgets
from tests.test_loop import code_reply, drive, failed, plan_reply, verdict_reply


def run_reply(code):
    return json.dumps({"run": code})


def test_a_two_step_plan_is_written_in_one_turn_and_applied_in_order():
    result, events, _, tools = drive(
        [
            plan_reply("30mm cube", "10mm hole"),
            code_reply("cube()", "hole()"),
            verdict_reply("pass"),
        ],
        agentic=True,
    )

    assert result.ok
    assert tools.applied_codes() == ["cube()", "hole()"]

    kinds = [event.kind for event in events]
    first_step = kinds.index(STEP_STARTED)
    first_applied = kinds.index(APPLIED)
    second_step = kinds.index(STEP_STARTED, first_applied)
    second_applied = kinds.index(APPLIED, second_step)
    assert first_step < first_applied < second_step < second_applied
    assert kinds.index(TOOL_CALL, first_step) < first_applied


def test_an_agentic_execute_turn_may_run_code_instead_of_only_looking():
    result, events, llm, tools = drive(
        [
            plan_reply("Add a box"),
            run_reply("2 + 2"),
            code_reply("box()"),
            verdict_reply("pass"),
        ],
        agentic=True,
    )

    assert result.ok
    assert tools.applied_codes() == ["box()"]
    run_calls = [event for event in events.of(TOOL_CALL) if event.data["name"] == "run"]
    assert len(run_calls) == 1


def test_a_step_that_fails_preflight_retries_up_to_attempts_per_step_and_the_count_is_asserted():
    result, events, _, tools = drive(
        [
            plan_reply("Only"),
            code_reply("try1()"),
            code_reply("try2()"),
            code_reply("try3()"),
            verdict_reply("pass"),
        ],
        applies=[failed("one"), failed("two")],
        agentic=True,
    )

    assert result.ok
    assert tools.applied_codes() == ["try1()", "try2()", "try3()"]
    failures = events.of(PREFLIGHT_FAILED)
    assert len(failures) == 2
    assert [f.data["attempt"] for f in failures] == [1, 2]
    kinds = [event.kind for event in events]
    assert kinds.index(PREFLIGHT_FAILED) < kinds.index(APPLIED)


def test_a_step_that_exhausts_its_attempts_hands_back_to_plan():
    result, events, _, tools = drive(
        [
            plan_reply("Only"),
            code_reply("try1()"),
            code_reply("try2()"),
            code_reply("try3()"),
            plan_reply("Only"),
            code_reply("try4()"),
            verdict_reply("pass"),
        ],
        applies=[failed("one"), failed("two"), failed("three")],
        budgets=Budgets(revisions=3),
        agentic=True,
    )

    assert result.ok
    assert tools.applied_codes() == ["try1()", "try2()", "try3()", "try4()"]
    failures = events.of(PREFLIGHT_FAILED)
    assert len(failures) == 3
    revised = events.first(PLAN_REVISED)
    assert revised is not None
    kinds = [event.kind for event in events]
    assert kinds.index(PREFLIGHT_FAILED) < kinds.index(PLAN_REVISED)
    assert kinds.count(PREFLIGHT_FAILED) == 3


def test_the_wrapper_off_never_touches_core_agent_ask(monkeypatch):
    """
    Execute with the wrapper off must not reach the agent seam.

    Plan reaches it on every run of its own accord, so refusing the whole
    module would only re-assert FCC-186. The claim this ticket owns is
    narrower: no call arrives naming the Execute phase.
    """
    from core import agent as agent_module

    real = agent_module.ask

    def watched(llm, events, phase, *args, **kwargs):
        assert phase != loop.EXECUTE_PHASE, "agentic=False must never call core.agent.ask"
        return real(llm, events, phase, *args, **kwargs)

    monkeypatch.setattr(agent_module, "ask", watched)

    result, events, _, tools = drive([
        plan_reply("30mm cube", "10mm hole"),
        code_reply("cube()", "hole()"),
        verdict_reply("pass"),
    ])

    assert result.ok
    assert tools.applied_codes() == ["cube()", "hole()"]
    assert events.first(loop.ERROR) is None


def test_the_wrapper_off_produces_the_same_event_sequence_as_the_wrapper_on():
    off_result, off_events, _, off_tools = drive([
        plan_reply("30mm cube", "10mm hole"),
        code_reply("cube()", "hole()"),
        verdict_reply("pass"),
    ])
    on_result, on_events, _, on_tools = drive(
        [
            plan_reply("30mm cube", "10mm hole"),
            code_reply("cube()", "hole()"),
            verdict_reply("pass"),
        ],
        agentic=True,
    )

    assert off_result.ok and on_result.ok
    assert off_tools.applied_codes() == on_tools.applied_codes() == ["cube()", "hole()"]
    off_kinds = [event.kind for event in off_events]
    on_kinds = [event.kind for event in on_events]
    assert off_kinds == on_kinds


def test_a_run_action_is_routed_through_the_main_thread_dispatcher():
    """
    `Namespace.run` (`core/agent.py`) is the one route an agentic Execute
    turn has to a live Document, through a name an earlier action bound. It
    tries `core.apply.on_main_thread` before anything else, and only falls
    back to a direct call where there is no Qt to marshal onto at all
    (`RuntimeError`, `ImportError`) -- the same fallback `utils/logger.py`
    already takes, and the one this test does not have to reproduce, since
    `tests/test_main_thread_dispatch.py` already covers it against a real Qt
    event loop.
    """
    import core.apply
    from core.agent import Namespace

    calls = []

    def stub(func, timeout=None):
        calls.append(func)
        return func()

    saved = core.apply.on_main_thread
    core.apply.on_main_thread = stub
    try:
        namespace = Namespace()
        output = namespace.run("print('mutating the Document')")
    finally:
        core.apply.on_main_thread = saved

    assert len(calls) == 1
    assert "mutating the Document" in output
