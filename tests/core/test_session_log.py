"""
Level 1 — the event stream as a session log.

"A bad run can be inspected without reproducing it" only holds if the log is
written as the run happens. A log flushed at the end is missing exactly the
runs worth reading.
"""
import json

import pytest

from core.events import (
    APPLIED, ATTACHED, BLOCKED, KINDS, PLAN, THOUGHT, Event, EventStream, replay,
)
from tests.event_helpers import events_of, first_event


def test_every_specced_event_kind_exists():
    assert set(KINDS) == {
        "intent", "thought", "writing", "question", "answer", "attached", "design",
        "part", "plan", "plan_revised", "gate", "step_started", "step_declared", "tool_call",
        "tool_result", "preflight_failed", "applied", "assertion", "outstanding", "unbuilt",
        "review_suspected", "review_verdict", "blocked", "stopped", "call_started", "cost",
        "error", "change_set", "routed", "completed", "aborted", "budget_exhausted", "failed",
        "sub_agent_spawned", "sub_agent_answered", "sub_agent_refused",
        "turn_budget_exhausted", "invoke_markup",
    }


def test_an_unknown_kind_is_a_loud_failure_not_a_silent_event():
    events = EventStream()

    with pytest.raises(ValueError, match="unknown event kind"):
        events.emit("almost_applied", step=1)


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


def test_an_attachment_survives_the_round_trip_by_name(tmp_path):
    """
    The whole point of recording it: a Session opened tomorrow can say the run
    had a picture and what it was called, whether or not the file is still
    there.
    """
    path = str(tmp_path / "session.jsonl")
    events = EventStream()
    events.log_to(path)

    events.emit(ATTACHED, files=[{"name": "mm2023-phase1.png",
                                  "path": "/tmp/gone/mm2023-phase1.png"}])
    events.emit(PLAN, steps=["Make a box"])

    replayed, truncated = replay(path)

    assert not truncated
    assert [event.kind for event in replayed] == [ATTACHED, PLAN]
    assert replayed[0].data["files"][0]["name"] == "mm2023-phase1.png"


def test_unserialisable_data_does_not_break_the_log(tmp_path):
    path = str(tmp_path / "session.jsonl")
    events = EventStream()
    events.log_to(path)

    events.emit(APPLIED, step=1, label="a", created=[object()])

    assert len(open(path).read().strip().splitlines()) == 1


def _written(path, events):
    stream = EventStream()
    stream.log_to(str(path))
    for kind, data in events:
        stream.emit(kind, **data)
    return stream


def test_a_session_replays_as_the_events_that_wrote_it(tmp_path):
    """A restored Session is the same stream, so it renders the same way."""
    path = tmp_path / "session.jsonl"
    written = _written(path, [
        (THOUGHT, {"phase": "plan", "text": "hmm"}),
        (PLAN, {"steps": ["Make a box", "Fillet it"]}),
        (APPLIED, {"step": 1, "label": "Box", "created": ["Box"]}),
    ])

    restored, truncated = replay(str(path))

    assert [e.kind for e in restored] == [e.kind for e in written]
    assert [e.data for e in restored] == [e.data for e in written]
    assert [e.at for e in restored] == [e.at for e in written]
    assert all(isinstance(e, Event) for e in restored)


def test_a_log_cut_off_mid_line_restores_everything_before_it(tmp_path):
    """The crash the per-event flush exists for must not cost the transcript."""
    path = tmp_path / "session.jsonl"
    _written(path, [(PLAN, {"steps": ["a"]}),
                    (APPLIED, {"step": 1, "label": "a", "created": []})])
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"kind": "blocked", "at": 1.0, "da')

    restored, truncated = replay(str(path))

    assert [e.kind for e in restored] == [PLAN, APPLIED]
    assert truncated, "the user was not told the transcript is incomplete"


def test_a_whole_log_is_not_reported_as_truncated(tmp_path):
    path = tmp_path / "session.jsonl"
    _written(path, [(PLAN, {"steps": ["a"]})])

    assert replay(str(path))[1] is False


def test_an_event_kind_this_build_does_not_know_survives_the_read(tmp_path):
    """An older log must not stop a Session opening; the renderer skips it."""
    path = tmp_path / "session.jsonl"
    _written(path, [(PLAN, {"steps": ["a"]})])
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"kind": "almost_applied", "at": 1.0, "data": {"step": 1}}\n')

    restored, truncated = replay(str(path))

    assert [e.kind for e in restored] == [PLAN, "almost_applied"]


def test_a_session_with_no_log_yet_replays_as_nothing(tmp_path):
    assert replay(str(tmp_path / "never_written.jsonl")) == ([], False)


def test_the_stream_can_be_queried_by_kind():
    events = EventStream()
    events.emit(THOUGHT, phase="plan", text="one")
    events.emit(PLAN, steps=["a"])
    events.emit(THOUGHT, phase="review", text="two")

    assert [e.data["text"] for e in events_of(events, THOUGHT)] == ["one", "two"]
    assert first_event(events, THOUGHT).data["text"] == "one"
    assert events.last(THOUGHT).data["text"] == "two"
    assert first_event(events, BLOCKED) is None
