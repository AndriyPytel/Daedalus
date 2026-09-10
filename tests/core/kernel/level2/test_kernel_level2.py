"""
Level 2 — the kernel's checkpoint contract, against a real headless FreeCAD.

Per this ticket's own TESTING STRATEGY: a passing Step's checkpoint carries
the new object, and a failing Step's reload leaves the prior checkpoint's
object count unchanged -- both verified by reopening the checkpoint file
from disk, not by reading the kernel's own claim about itself. Also here: a
worker killed mid-run, or timed out, resumes the next Step on the last
checkpoint rather than an empty Document, and a worker that dies before any
Step landed is refused rather than served one.
"""
import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad

CRASHING_CODE = "import ctypes; ctypes.string_at(0)"


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def _objects_in(path):
    import FreeCAD

    doc = FreeCAD.openDocument(path)
    try:
        return sorted(o.Name for o in doc.Objects)
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_a_single_subprocess_lifecycle_serves_two_steps(kernel):
    """AC1: one Step costs one execution, not a spawn-per-check pattern."""
    pid = kernel.pid

    first = kernel.check("doc.addObject('Part::Box', 'A')")
    second = kernel.check("doc.addObject('Part::Box', 'B')")

    assert first.ok and second.ok
    assert kernel.pid == pid, "a Step cost a new subprocess instead of reusing the kernel"


def test_a_passing_steps_checkpoint_contains_the_new_object(kernel):
    result = kernel.check("doc.addObject('Part::Box', 'Box')")

    assert result.ok, result.traceback
    assert result.checkpoint
    assert _objects_in(result.checkpoint) == ["Box"]


def test_a_failing_steps_reload_leaves_the_prior_checkpoints_object_count_unchanged(kernel):
    passed = kernel.check("doc.addObject('Part::Box', 'Box')")
    assert passed.ok, passed.traceback
    before = _objects_in(passed.checkpoint)

    failed = kernel.check(
        "doc.addObject('Part::Box', 'Extra')\nassert False, 'this Step is wrong'"
    )

    assert not failed.ok
    assert failed.checkpoint == passed.checkpoint
    assert _objects_in(failed.checkpoint) == before, \
        "the reload left the failed Step's object behind"


def test_a_read_only_failing_step_does_not_rewind_the_kernel(kernel):
    """
    AC5: a Step that raises without touching the Document must not undo an
    earlier, passing Step -- only a Step whose own diff is non-empty rewinds.
    """
    passed = kernel.check("doc.addObject('Part::Box', 'Box')")
    assert passed.ok, passed.traceback

    read_only_failure = kernel.check("print(doc.NotAnAttribute)")
    assert not read_only_failure.ok

    still_there = kernel.check(
        "assert doc.getObject('Box') is not None, "
        "'a read-only failure rewound the kernel'"
    )
    assert still_there.ok, still_there.traceback


def test_a_second_step_sees_the_first_steps_object_in_the_live_document(kernel):
    """
    The session continues across Steps: the second Step works against the same
    live Document the first one changed, without reopening it from disk.
    """
    first = kernel.check("doc.addObject('Part::Box', 'FromFirst')")
    assert first.ok, first.traceback

    second = kernel.check(
        "assert doc.getObject('FromFirst') is not None, "
        "'the first Step object is not in the live document'"
    )

    assert second.ok, second.traceback


def test_a_step_addresses_an_earlier_steps_object_through_the_name_map_after_a_collision(kernel):
    """
    AC4: a Step's preferred name is already taken -- by an earlier Step, here
    standing in for a user's hand-edit -- and the next Step still reaches the
    right object, through `names` rather than the literal string.
    """
    first = kernel.check("doc.addObject('Part::Box', 'Box')")
    assert first.ok, first.traceback

    collided = kernel.check(
        "box = doc.addObject('Part::Box', 'Box')\n"
        "box.Length = 42\n"
    )
    assert collided.ok, collided.traceback
    assert collided.created == ["Box001"], \
        "FreeCAD did not rename the colliding object the way this test assumes"

    addressed = kernel.check(
        "target = doc.getObject(names['Box'])\n"
        "assert target.Length.Value == 42, 'reached the wrong Box'\n"
    )

    assert addressed.ok, addressed.traceback


def test_a_name_map_addition_from_a_failed_step_does_not_survive_the_reload(kernel):
    first = kernel.check("doc.addObject('Part::Box', 'Box')")
    assert first.ok, first.traceback

    failed = kernel.check(
        "doc.addObject('Part::Box', 'Extra')\n"
        "assert False, 'this Step is wrong'\n"
    )
    assert not failed.ok

    after = kernel.check("assert 'Extra' not in names, names")
    assert after.ok, after.traceback


def test_a_worker_killed_mid_run_resumes_the_next_step_on_the_last_checkpoint(kernel):
    first = kernel.check("doc.addObject('Part::Box', 'FromFirst')")
    assert first.ok, first.traceback
    doomed = kernel.pid

    crashed = kernel.check(CRASHING_CODE)
    assert not crashed.ok, "the crash should be reported as this Step's own failure"
    assert kernel.pid != doomed, "the worker was not replaced"

    second = kernel.check(
        "assert doc.getObject('FromFirst') is not None, "
        "'the resumed kernel does not see the checkpointed object'"
    )

    assert second.ok, second.traceback


def test_a_timeout_resumes_the_next_step_on_the_last_checkpoint_too(kernel):
    first = kernel.check("doc.addObject('Part::Box', 'FromFirst')")
    assert first.ok, first.traceback
    hung = kernel.pid

    timed_out = kernel.check("while True:\n    pass", timeout=0.5)
    assert not timed_out.ok
    assert kernel.pid != hung, "the worker was not replaced"

    second = kernel.check(
        "assert doc.getObject('FromFirst') is not None, "
        "'the resumed kernel does not see the checkpointed object'"
    )

    assert second.ok, second.traceback


def test_a_crash_before_any_step_lands_is_refused_not_served_as_an_empty_session(kernel):
    doomed = kernel.pid

    crashed = kernel.check(CRASHING_CODE)
    assert not crashed.ok
    assert kernel.pid != doomed, "the worker was not replaced"

    refused = kernel.check("doc.addObject('Part::Box', 'Box')")

    assert not refused.ok, \
        "an empty Document with no checkpoint behind it was served as if it were a session"
    assert "checkpoint" in refused.traceback.lower()
