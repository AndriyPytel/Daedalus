"""
The typed event stream.

The loop emits; the panel renders. The panel never calls loop methods, so the
loop has no idea a UI exists and the same stream doubles as the session log —
a bad run can be inspected without reproducing it.

The stream is an output. It is asserted on and never substituted.

Two kinds say where a run came from rather than what it did: `part` carries the
path the reading Design settled on was written to, so a run can be pointed at
the file it was planned from, and `outstanding` carries what an earlier run's
acceptance checks found wrong and nobody fixed, emitted once at the top of the
run that inherits it.

`unbuilt` is a Step that could not be built and that nothing in the geometry
was waiting on, with the entries of the description it was the Step to satisfy:
a run that finishes with one of those is not a blocked run, and the stream says
so in a word of its own.

`intent` is what the run was asked to produce. It stands first, before anything
has been read, so a session says what was wanted before it says what was found
and a transcript can be judged against the request that opened it.
"""
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple

INTENT = "intent"
THOUGHT = "thought"
QUESTION = "question"
ANSWER = "answer"
ATTACHED = "attached"
DESIGN = "design"
PART = "part"
PLAN = "plan"
PLAN_REVISED = "plan_revised"
GATE = "gate"
STEP_STARTED = "step_started"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
PREFLIGHT_FAILED = "preflight_failed"
APPLIED = "applied"
ASSERTION = "assertion"
OUTSTANDING = "outstanding"
UNBUILT = "unbuilt"
REVIEW_VERDICT = "review_verdict"
BLOCKED = "blocked"
ERROR = "error"

KINDS = (
    INTENT, THOUGHT, QUESTION, ANSWER, ATTACHED, DESIGN, PART, PLAN, PLAN_REVISED,
    GATE, STEP_STARTED, TOOL_CALL, TOOL_RESULT, PREFLIGHT_FAILED, APPLIED,
    ASSERTION, OUTSTANDING, UNBUILT, REVIEW_VERDICT, BLOCKED, ERROR,
)


@dataclass(frozen=True)
class Event:
    kind: str
    data: Dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)

    def as_json(self) -> str:
        return json.dumps({"kind": self.kind, "at": self.at, "data": self.data},
                          default=str, sort_keys=True)


class EventStream:
    """
    An append-only log with live subscribers.

    Subscribing is how the panel watches; iterating is how tests and the
    session log read it back.
    """

    def __init__(self, subscribers: List[Callable] = None):
        self._events: List[Event] = []
        self._subscribers: List[Callable] = list(subscribers or ())

    def subscribe(self, callback: Callable):
        self._subscribers.append(callback)
        return callback

    def emit(self, kind: str, **data) -> Event:
        """
        Record an event of *kind* and hand it to every subscriber.

        A subscriber that raises is ignored: a broken renderer must not take the
        run down with it.
        """
        if kind not in KINDS:
            raise ValueError("unknown event kind: %r" % kind)
        event = Event(kind=kind, data=data)
        self._events.append(event)
        for subscriber in self._subscribers:
            try:
                subscriber(event)
            except Exception:
                pass
        return event

    def __iter__(self):
        return iter(tuple(self._events))

    def __len__(self):
        return len(self._events)

    def __getitem__(self, index):
        return self._events[index]

    def of(self, kind: str) -> List[Event]:
        return [event for event in self._events if event.kind == kind]

    def first(self, kind: str):
        for event in self._events:
            if event.kind == kind:
                return event
        return None

    def last(self, kind: str):
        for event in reversed(self._events):
            if event.kind == kind:
                return event
        return None

    def as_log(self) -> str:
        """One JSON object per line — the session log."""
        return "\n".join(event.as_json() for event in self._events)

    def log_to(self, path: str):
        """
        Append every event to *path* as it happens.

        The stream is the session log, so a bad run can be inspected without
        reproducing it. Written per event rather than at the end, because the
        runs worth reading are the ones that did not finish cleanly.
        """
        handle = open(path, "a", encoding="utf-8")

        def write(event):
            handle.write(event.as_json() + "\n")
            handle.flush()

        for event in self._events:
            write(event)
        self.subscribe(write)
        return handle


def replay(path) -> Tuple[List[Event], bool]:
    """
    Read a session log back as the events that wrote it, and whether it was cut off.

    A crash cuts the last line in half — that is the case the per-event flush
    exists for, so a partial line is dropped and everything before it survives.
    A Session is never refused because its last event did not finish writing,
    and a kind this build does not know is carried through for the renderer to
    skip, the same way it skips one live.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return [], False

    events, truncated = [], False
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            truncated = True
            continue
        truncated = False
        if isinstance(entry, dict) and entry.get("kind"):
            events.append(Event(kind=entry["kind"], data=entry.get("data") or {},
                                at=entry.get("at") or 0.0))
    return events, truncated
