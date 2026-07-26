"""
Level 1 — the event stream as a session log.

"A bad run can be inspected without reproducing it" only holds if the log is
written as the run happens. A log flushed at the end is missing exactly the
runs worth reading.
"""
import json

import pytest

from core.events import APPLIED, BLOCKED, KINDS, PLAN, THOUGHT, EventStream


def test_every_specced_event_kind_exists():
    assert set(KINDS) == {
        "thought", "plan", "plan_revised", "step_started", "tool_call", "tool_result",
        "preflight_failed", "render", "applied", "review_verdict", "blocked", "error",
    }


def test_an_unknown_kind_is_a_loud_failure_not_a_silent_event():
    events = EventStream()

    with pytest.raises(ValueError, match="unknown event kind"):
        events.emit("almost_applied", step=1)


# ── live watching ─────────────────────────────────────────────────────────────


def test_subscribers_see_events_as_they_are_emitted():
    seen = []
    events = EventStream()
    events.subscribe(seen.append)

    events.emit(PLAN, steps=["a"])
    assert [e.kind for e in seen] == [PLAN]

    events.emit(APPLIED, step=1, label="a", created=[])
    assert [e.kind for e in seen] == [PLAN, APPLIED]


def test_a_broken_renderer_does_not_take_the_run_down():
    events = EventStream()
    events.subscribe(lambda event: 1 / 0)
    reached = []
    events.subscribe(reached.append)

    events.emit(PLAN, steps=["a"])

    assert len(reached) == 1, "a later subscriber was skipped"
    assert len(events) == 1


# ── persistence ───────────────────────────────────────────────────────────────


def test_the_log_is_written_as_the_run_happens(tmp_path):
    path = str(tmp_path / "session.jsonl")
    events = EventStream()
    events.log_to(path)

    events.emit(PLAN, steps=["Make a box"])
    mid_run = open(path).read()

    events.emit(BLOCKED, reason="out of attempts", tried=[])

    assert "Make a box" in mid_run, "nothing was on disk until the run ended"
    assert len(open(path).read().strip().splitlines()) == 2


def test_events_already_emitted_are_not_lost_when_logging_starts_late(tmp_path):
    path = str(tmp_path / "session.jsonl")
    events = EventStream()
    events.emit(THOUGHT, phase="plan", text="early")

    events.log_to(path)
    events.emit(PLAN, steps=["a"])

    kinds = [json.loads(line)["kind"] for line in open(path).read().strip().splitlines()]
    assert kinds == [THOUGHT, PLAN]


def test_a_logged_run_reads_back_as_the_run(tmp_path):
    path = str(tmp_path / "session.jsonl")
    events = EventStream()
    events.log_to(path)

    events.emit(PLAN, steps=["Make a box", "Fillet it"])
    events.emit(BLOCKED, reason="Step 2 failed 3 times",
                tried=[{"step": "Fillet it", "code": "x()", "traceback": "boom"}])

    replayed = [json.loads(line) for line in open(path).read().strip().splitlines()]

    assert replayed[0]["data"]["steps"] == ["Make a box", "Fillet it"]
    assert replayed[1]["data"]["tried"][0]["traceback"] == "boom"
    assert all("at" in entry for entry in replayed), "no timestamps to order a bad run by"


def test_unserialisable_data_does_not_break_the_log(tmp_path):
    path = str(tmp_path / "session.jsonl")
    events = EventStream()
    events.log_to(path)

    events.emit(APPLIED, step=1, label="a", created=[object()])

    assert len(open(path).read().strip().splitlines()) == 1


# ── reading it back in memory ─────────────────────────────────────────────────


def test_the_stream_can_be_queried_by_kind():
    events = EventStream()
    events.emit(THOUGHT, phase="plan", text="one")
    events.emit(PLAN, steps=["a"])
    events.emit(THOUGHT, phase="review", text="two")

    assert [e.data["text"] for e in events.of(THOUGHT)] == ["one", "two"]
    assert events.first(THOUGHT).data["text"] == "one"
    assert events.last(THOUGHT).data["text"] == "two"
    assert events.first(BLOCKED) is None
