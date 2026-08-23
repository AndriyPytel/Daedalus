"""
`run()`'s own tri-state resolution of `RLM_AGENTIC` (FCC-192).

Before this ticket, `ui/copilot_panel.py` imported `core.loop ::
agentic_from_env` and resolved it itself before calling `run`. That broke
`tests/test_panel_renders_the_stream.py ::
test_the_panel_only_ever_asks_the_loop_to_start` -- the panel may only
import `run` and the loop's data types, never a decision the loop makes for
itself. `run`'s own `agentic` parameter is tri-state instead: `None`, the
default, means "ask the environment", resolved once inside `run`; `True` or
`False` from a caller is taken as given and never reads the environment.

No FreeCAD anywhere in this file: `execute` is monkeypatched to capture what
`agentic` it was actually called with, rather than let a real Step run.
"""
from core import loop
from core.loop import Budgets, Written, run
from tests.test_loop import FakeTools, code_reply, design_reply, everything, plan_reply, \
                            verdict_reply, A_CUBE
from core.llm import ScriptedLLMClient


def _run_and_capture_agentic(monkeypatch, env=None, agentic=None):
    seen = []

    def fake_execute(*args, **kwargs):
        seen.append(kwargs.get("agentic"))
        return Written(steps={})

    monkeypatch.setattr(loop, "execute", fake_execute)
    if env is None:
        monkeypatch.delenv("RLM_AGENTIC", raising=False)
    else:
        monkeypatch.setenv("RLM_AGENTIC", env)

    llm = ScriptedLLMClient([
        design_reply(A_CUBE),
        plan_reply("Add a box", satisfies=everything(A_CUBE)),
    ])
    tools = FakeTools()
    kwargs = {} if agentic is None else {"agentic": agentic}
    run("Make a 10mm box", llm=llm, tools=tools.registry, budgets=Budgets(), **kwargs)
    return seen[0] if seen else None


def test_unset_reproduces_todays_shipped_mix_execute_off(monkeypatch):
    assert _run_and_capture_agentic(monkeypatch, env=None) is False


def test_rlm_agentic_off_keeps_execute_off(monkeypatch):
    assert _run_and_capture_agentic(monkeypatch, env="off") is False


def test_rlm_agentic_on_turns_execute_on(monkeypatch):
    assert _run_and_capture_agentic(monkeypatch, env="on") is True


def test_a_caller_that_passes_agentic_is_never_asked_the_environment(monkeypatch):
    """`RLM_AGENTIC=off` in the environment must not override a caller who
    explicitly said `True` -- an explicit value is taken as given, the same
    way `traps` being handed an open store is."""
    assert _run_and_capture_agentic(monkeypatch, env="off", agentic=True) is True
    assert _run_and_capture_agentic(monkeypatch, env="on", agentic=False) is False


def test_the_panel_no_longer_imports_or_resolves_the_variable_itself():
    """`ui/copilot_panel.py` reads `AGENTS.md`'s own fence and stops at
    `run`; resolving `RLM_AGENTIC` is the loop's job now, not the panel's."""
    import ast
    import os

    path = os.path.join(os.path.dirname(loop.__file__), "..", "ui", "copilot_panel.py")
    tree = ast.parse(open(path, encoding="utf-8").read())

    imported = {alias.name for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom) and node.module == "core.loop"
               for alias in node.names}

    assert "agentic_from_env" not in imported
