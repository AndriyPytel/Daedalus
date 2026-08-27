"""
Level 1 — the traceback a Step's own frames come back as, with no FreeCAD in play.

`preflight()` and `run_in_transaction()` both compile a Step's code under the
name "<step>" and exec it inside their own function. Left alone, the
traceback that walks back out carries their own exec(compile(...)) frame on
top — the worker's code, not the Step's — and the frame that actually failed,
`File "<step>", line N`, never printed a source line, because "<step>" is
not a real file and `linecache` had nothing to look it up in.

`register_step_source` and `trimmed_traceback` are the two calls that fix
that, and neither needs FreeCAD to prove it: `exec(compile(code, "<step>",
"exec"), {})` reproduces the exact shape a Step's failure takes without
starting a worker.
"""
from core.preflight_worker import register_step_source, trimmed_traceback

STEP_CODE = (
    "def make():\n"
    "    assert False, 'boom'\n"
    "make()\n"
)


def _raised(code):
    register_step_source(code)
    try:
        exec(compile(code, "<step>", "exec"), {})  # noqa: S102 — reproduces the worker's own call
    except BaseException as exc:
        return exc
    raise AssertionError("the step code did not raise")


def test_the_workers_own_exec_frame_is_cut_from_the_traceback():
    formatted = trimmed_traceback(_raised(STEP_CODE))

    assert 'File "<step>"' in formatted
    assert "exec(compile(code" not in formatted
    assert "_raised" not in formatted


def test_the_step_frame_prints_the_line_that_actually_failed():
    formatted = trimmed_traceback(_raised(STEP_CODE))

    assert "assert False, 'boom'" in formatted


def test_an_exception_with_no_step_frame_keeps_its_traceback_instead_of_going_blank():
    try:
        raise ValueError("nowhere near a step")
    except ValueError as exc:
        formatted = trimmed_traceback(exc)

    assert "nowhere near a step" in formatted
    assert "File " in formatted
