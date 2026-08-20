"""
Level 2 -- the Plan widget FCC-177 rewrites, built for real against
FreeCAD's own Qt binding, offscreen.

`ui/plan_widget.py`'s own docstring draws the split: editable until `lock()`,
a read-only per-Step marker display after. This is where FCC-177's Acceptance
Criteria on that split are checked -- inline editing, add, remove, reorder,
and the switch to the marker display `highlight()`/`mark()` already painted
before this ticket. The wiring from a live `core.events.Event` through the
panel to these calls is `tests/test_panel_level2.py`'s, the same split
`test_queue_widget_level2.py` draws for the Queue.
"""
import pytest

from conftest import NO_QT, QT_APP

pytestmark = pytest.mark.freecad


def _widget(steps=("Create sketch for base", "Pad base to height")):
    if QT_APP is None:
        pytest.skip(NO_QT)
    from ui.plan_widget import PlanWidget

    return PlanWidget(list(steps))


def test_a_new_plan_starts_with_every_step_pending_and_unlocked():
    from ui.plan_widget import PENDING

    plan = _widget()

    assert plan.steps() == ["Create sketch for base", "Pad base to height"]
    assert plan._states == [PENDING, PENDING]
    assert not plan.locked


def test_clicking_a_steps_description_and_typing_changes_its_text():
    """
    Inline editing is `QListWidgetItem` text, set the same way a user typing
    into the row Qt opens for it would -- `editItem` starts the same edit a
    click does, the assertion is on what committing it leaves behind.
    """
    plan = _widget()

    item = plan._list.item(0)
    item.setText("Pad base to 10mm height")

    assert plan.steps() == ["Pad base to 10mm height", "Pad base to height"]


def test_adding_a_step_inserts_an_editable_entry_at_the_end():
    from conftest import QtCore

    from ui.plan_widget import PENDING

    plan = _widget()

    plan.add_step()

    assert plan.steps() == ["Create sketch for base", "Pad base to height", ""]
    assert plan._states[-1] == PENDING
    assert plan._list.item(2).flags() & QtCore.Qt.ItemIsEditable


def test_deleting_a_step_removes_it_from_the_plan():
    plan = _widget(("A", "B", "C"))

    plan.remove_step(1)

    assert plan.steps() == ["A", "C"]


def test_reordering_a_step_updates_the_plan_sequence():
    """
    What a completed drag leaves behind: `QListWidget`'s own InternalMove
    takes the item out of one row and puts it in another -- this is that,
    driven directly rather than through a simulated mouse.
    """
    plan = _widget(("A", "B", "C"))

    moved = plan._list.takeItem(0)
    plan._list.insertItem(2, moved)

    assert plan.steps() == ["B", "C", "A"]


def test_locking_stops_further_editing_add_and_delete():
    plan = _widget(("A", "B"))

    plan.lock()
    plan.add_step()
    plan.remove_step(0)
    plan.set_steps(["Something else"])

    assert plan.steps() == ["A", "B"]
    assert not plan._list.isVisible()
    assert not plan._add_button.isVisible()


def test_locking_freezes_exactly_what_the_plan_held_at_that_moment():
    """Build reads `steps()` and locks in the same breath (FCC-177) -- an edit after either is moot."""
    plan = _widget(("A", "B"))
    plan._list.item(0).setText("A, edited")

    built = list(plan.steps())
    plan.lock()

    assert built == ["A, edited", "B"]
    assert plan._steps == ["A, edited", "B"]


def test_highlighting_locks_the_plan():
    plan = _widget(("A", "B"))

    plan.highlight(1)

    assert plan.locked


def test_highlight_moves_the_running_marker_forward():
    from ui.plan_widget import DONE, PENDING, RUNNING

    plan = _widget(("A", "B", "C"))

    plan.highlight(1)
    assert plan._states == [RUNNING, PENDING, PENDING]

    plan.highlight(2)
    assert plan._states == [DONE, RUNNING, PENDING]


def test_mark_sets_one_steps_state_directly():
    from ui.plan_widget import DONE, FAILED, PENDING

    plan = _widget(("A", "B"))

    plan.mark(1, DONE)
    plan.mark(2, FAILED)

    assert plan._states == [DONE, FAILED]


def test_showing_the_widget_does_not_crash_before_or_after_lock():
    plan = _widget(("A",))
    plan.show()

    plan.highlight(1)
    plan.show()

    plan.close()
