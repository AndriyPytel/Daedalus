"""Where nobody answers, a run whose final shape is not valid ends on the last Step whose shape was."""
from core.events import ANSWER
from core.loop.gate import nobody_to_ask
from tests.event_helpers import events_of
from tests.loop_helpers import code_reply, drive, nothing_to_add, plan_reply


def _run(shapes, ask=nobody_to_ask):
    return drive([plan_reply("Pad the outline 10 mm", "Pad the boss 5 mm", "Pad the lug 3 mm"),
                  code_reply("a()", "b()", "c()"), nothing_to_add()],
                 shapes=shapes, ask=ask)


def test_steps_are_undone_until_the_shape_is_valid_and_the_run_says_so():
    result, events, _, tools = _run([False, False, True])

    assert result.ok and result.steps_applied == 1
    assert [name for name, _ in tools.calls].count("undo_step") == 2
    assert "final shape invalid, saved Step 1" in result.reason
    assert any("saved Step 1" in e.data["text"] for e in events_of(events, ANSWER))


def test_a_valid_shape_is_left_alone():
    result, _, _, tools = _run([True])

    assert result.steps_applied == 3 and "undo_step" not in [name for name, _ in tools.calls]
