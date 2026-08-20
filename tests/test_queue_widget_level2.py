"""
Level 2 -- the Queue widget FCC-172 adds, built for real against FreeCAD's
own Qt binding, offscreen.

The Queue is a pure renderer (`ui/queue_widget.py`'s own docstring): it is
told what happened through the small verbs `copilot_panel.py` calls from
`on_event`, and this is where those verbs are checked against FCC-172's
Acceptance Criteria. The wiring from a live `core.events.Event` to those
verbs is `tests/test_panel_level2.py`'s, the same split `test_icon_set_level2.py`
draws between an asset and the panel that reads it.
"""
import pytest

from conftest import NO_QT, QT_APP

pytestmark = pytest.mark.freecad


def _widget():
    if QT_APP is None:
        pytest.skip(NO_QT)
    from ui.queue_widget import QueueWidget

    return QueueWidget()


def test_starting_a_run_shows_design_running_and_the_rest_queued():
    from ui.queue_widget import DESIGN, EXECUTE, PLAN, QUEUED, REVIEW, RUNNING, ASSERTION

    queue = _widget()

    assert queue.phase_state(DESIGN) == RUNNING
    assert queue.phase_state(PLAN) == QUEUED
    assert queue.phase_state(EXECUTE) == QUEUED
    assert queue.phase_state(ASSERTION) == QUEUED
    assert queue.phase_state(REVIEW) == QUEUED
    assert queue.step_count() == 0


def test_completing_design_and_starting_plan_moves_design_to_done_and_plan_to_running():
    from ui.queue_widget import DESIGN, DONE, PLAN, RUNNING

    queue = _widget()
    queue.start_plan(["Make a box", "Fillet it"])

    assert queue.phase_state(DESIGN) == DONE
    assert queue.phase_state(PLAN) == RUNNING


def test_entering_execute_lists_the_foreseen_steps_queued_until_they_start():
    from ui.queue_widget import EXECUTE, PLAN, QUEUED, RUNNING, DONE

    queue = _widget()
    queue.start_plan(["Create sketch for base", "Pad the sketch"])
    queue.start_execute()

    assert queue.phase_state(PLAN) == DONE
    assert queue.phase_state(EXECUTE) == RUNNING
    assert queue.step_count() == 2
    assert queue.step_state(1) == QUEUED
    assert queue.step_state(2) == QUEUED


def test_start_execute_takes_the_gates_steps_over_what_plan_foresaw():
    """
    The Plan Gate is edited directly now (FCC-177): if Build changed the
    count or the text, the Queue's own foreseen list is the one thing left
    still describing the unedited Plan unless the approved Gate hands its
    Steps to `start_execute` too.
    """
    from ui.queue_widget import QUEUED

    queue = _widget()
    queue.start_plan(["Create sketch for base", "Pad the sketch"])
    queue.start_execute(["Create sketch for base", "Pad the sketch", "Fillet the top edge"])

    assert queue.step_count() == 3
    assert queue.step_state(3) == QUEUED


def test_a_step_starting_moves_it_from_queued_to_running():
    from ui.queue_widget import RUNNING

    queue = _widget()
    queue.start_plan(["Create sketch for base", "Pad the sketch"])
    queue.start_execute()
    queue.step_started(1)

    assert queue.step_state(1) == RUNNING


def test_a_second_step_starting_leaves_the_first_reported_done():
    from ui.queue_widget import DONE, RUNNING

    queue = _widget()
    queue.start_plan(["Create sketch for base", "Pad the sketch"])
    queue.start_execute()
    queue.step_started(1)
    queue.step_applied(1)
    queue.step_started(2)

    assert queue.step_state(1) == DONE
    assert queue.step_state(2) == RUNNING


def test_a_step_completing_moves_it_to_done():
    from ui.queue_widget import DONE

    queue = _widget()
    queue.start_plan(["Create sketch for base", "Pad the sketch"])
    queue.start_execute()
    queue.step_started(1)
    queue.step_applied(1)

    assert queue.step_state(1) == DONE


def test_completing_execute_collapses_it_and_expands_assertion_to_running():
    from ui.queue_widget import ASSERTION, DONE, EXECUTE, RUNNING

    queue = _widget()
    queue.start_plan(["Create sketch for base"])
    queue.start_execute()
    queue.step_started(1)
    queue.step_applied(1)

    assert queue.phase_state(EXECUTE) == DONE
    assert queue.phase_state(ASSERTION) == RUNNING
    assert queue._steps_container.isVisible() is False


def test_an_unbuilt_last_step_still_completes_execute():
    from ui.queue_widget import ASSERTION, EXECUTE, DONE, RUNNING

    queue = _widget()
    queue.start_plan(["Add a chamfer nothing needs"])
    queue.start_execute()
    queue.step_started(1)
    queue.step_unbuilt(1)

    assert queue.phase_state(EXECUTE) == DONE
    assert queue.phase_state(ASSERTION) == RUNNING


def test_completing_review_shows_all_five_phases_done():
    from ui.queue_widget import ASSERTION, DESIGN, DONE, EXECUTE, PLAN, REVIEW

    queue = _widget()
    queue.start_plan(["Create sketch for base"])
    queue.start_execute()
    queue.step_started(1)
    queue.step_applied(1)
    queue.start_review()
    queue.finish_review()

    for phase in (DESIGN, PLAN, EXECUTE, ASSERTION, REVIEW):
        assert queue.phase_state(phase) == DONE, phase


def test_a_run_failing_in_plan_leaves_execute_assertion_review_queued():
    from ui.queue_widget import ASSERTION, EXECUTE, FAILED, PLAN, QUEUED, REVIEW

    queue = _widget()
    queue.start_plan(["Create sketch for base"])
    queue.fail_current()

    assert queue.phase_state(PLAN) == FAILED
    assert queue.phase_state(EXECUTE) == QUEUED
    assert queue.phase_state(ASSERTION) == QUEUED
    assert queue.phase_state(REVIEW) == QUEUED


def test_a_failed_phase_does_not_advance_when_the_run_carries_on_regardless():
    """
    `fail_current` stops the run, so nothing downstream should ever call
    another `start_*`/`step_*` on this widget again -- but if it did, a
    Failed phase must not read as Done under it.
    """
    from ui.queue_widget import FAILED, PLAN

    queue = _widget()
    queue.start_plan(["Create sketch for base"])
    queue.fail_current()
    queue.start_plan(["Create sketch for base, revised"])

    assert queue.phase_state(PLAN) == FAILED
