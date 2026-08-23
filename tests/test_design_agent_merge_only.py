"""
FCC-185 — the merge-only invariant (ADR-0019), proven rather than argued: once
a Design turn's reading has been written to disk (`core/part_file.py :: saved`,
called exactly once per run, from `core/loop.py :: _written_down`), nothing
later in the run — Plan, Execute, Assertion, Review — writes to that file
again. `execute()` never opens `part_file` at all; the structural half of this
test proves that by reading the source, the way `test_loop.py ::
test_nothing_past_the_reading_opens_the_evidence` already proves the
neighbouring claim about `logged`/`evidence`.
"""
import ast
import hashlib
import inspect
import os

from core.events import PART
from core.llm import ScriptedLLMClient
from core.loop import Budgets, execute
from tests.test_loop import (
    A_CUBE, FakeTools, code_reply, design_reply, drive, everything, plan_reply,
    verdict_reply,
)


def _fingerprint(path):
    with open(path, "rb") as handle:
        return os.stat(path).st_mtime_ns, hashlib.sha256(handle.read()).hexdigest()


def test_a_design_turns_written_reading_is_untouched_by_a_later_execute_call():
    """
    A whole run writes the reading once; a further `execute()` call against a
    scripted Plan that never mentions the file leaves its mtime and its
    content hash exactly as the run left them.
    """
    _, events, _, _ = drive([
        design_reply(A_CUBE),
        plan_reply("Only", satisfies=everything(A_CUBE)),
        code_reply("box()"),
        verdict_reply("pass"),
    ])

    written = events.last(PART).data["path"]
    before = _fingerprint(written)

    tools = FakeTools()
    execute(["Only"], tools.registry, ScriptedLLMClient([code_reply("box()")]),
           events, budgets=Budgets())

    assert _fingerprint(written) == before


def _reaches(function, called):
    """Whether *function*'s body names `part_file.<called>` anywhere in it."""
    return any(isinstance(node, ast.Attribute) and node.attr == called
              and isinstance(node.value, ast.Name) and node.value.id == "part_file"
              for node in ast.walk(function))


def test_part_file_saved_is_called_from_written_down_and_nowhere_else_in_the_loop():
    """
    `part_file.saved` is the one function that puts the reading on disk. Read
    off the source of `core/loop.py`: every `def` in the module, and which of
    them names `part_file.saved` — the answer is one name, `_written_down`.
    """
    from core import loop

    with open(loop.__file__, encoding="utf-8") as source:
        tree = ast.parse(source.read())

    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    assert {function.name for function in functions if _reaches(function, "saved")} \
        == {"_written_down"}


def test_design_and_the_agent_and_apply_never_import_part_file():
    """
    Design has no direct file-write capability to invariant-check in the first
    place (FCC-185): `core/design.py`, `core/agent.py` and `core/apply.py` —
    the phase, its agent wrapper, and Execute's own code path onto the
    Document — carry no `part_file` import at all.
    """
    from core import agent, apply, design

    for module in (design, agent, apply):
        source = inspect.getsource(module)
        assert "part_file" not in source, "%s reaches part_file" % module.__name__
