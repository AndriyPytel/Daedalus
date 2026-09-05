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

`call_started` marks when a provider call begins, carrying the phase, model,
and timeout ceiling in seconds when one is set (subscription route only). It is
emitted before the stream opens and pairs with the `cost` that follows: a
`call_started` with no matching `cost` after it is a call that died without
answering, which is the log-side answer to whether a silent call hung.

`cost` is what one call to the provider spent — the wall-clock time and the
amount, when a price is known. The `seconds` field is always present, measured
from when the call opened to when it completed. The `usd` field is present only
when the client reported a price: a provider that answers on a subscription is
not billed by the token, so the number is what the same call would have cost
through a key, and a scripted run emits no price at all. The panel accumulates
the cost into one running total for the run rather than narrating each call: a
hundred Design calls would otherwise be a hundred lines nobody reads
(`ui/copilot_panel.py :: on_event`).

`writing` is the reply forming, chunk by chunk, the way `thought` is the
reasoning before it. It is a kind of its own because the two are read
differently — reasoning is the model deciding and may be withheld by the
route, the reply is what the phase will be held to — and because the wait it
accounts for is the larger one: 22.4 minutes of a recorded run's 23.6 minutes
of provider time fell after the last `thought` of every call.

`stopped` is the user's own `Stopped` reaching the loop's boundary
(`core/loop.py :: run`), not a `blocked`: the run did not fail to finish,
it was told to stop. FCC-292: a genuine block and a user cancel used to
share the `blocked` kind, so `core/measure.py`'s blocked rate read a
person clicking Stop as indistinguishable from a run that ran out of road.

`change_set` is `core.design.diff`'s answer to what a new Design version
changed since the one Plan last saw, carried on the stream the same turn
Plan is handed it in place of the whole document (FCC-311): a moved datum on
the stream as its own entry is the record of why a re-plan cited what it
cited, without a reader re-deriving the diff from two `design` events by hand.

`routed` is the orchestrator's own decision (`core/orchestrator.py`) -- which
agent a message was classified as belonging to -- as its own entry, separate
from the `intent` or other verbatim text it was handed alongside: a reader
sees what the orchestrator decided without it being mixed into the message
the user actually typed.
"""
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple

INTENT = "intent"
THOUGHT = "thought"
WRITING = "writing"
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
REVIEW_SUSPECTED = "review_suspected"
BLOCKED = "blocked"
STOPPED = "stopped"
CALL_STARTED = "call_started"
COST = "cost"
ERROR = "error"
CHANGE_SET = "change_set"
ROUTED = "routed"

KINDS = (
    INTENT, THOUGHT, WRITING, QUESTION, ANSWER, ATTACHED, DESIGN, PART, PLAN, PLAN_REVISED,
    GATE, STEP_STARTED, TOOL_CALL, TOOL_RESULT, PREFLIGHT_FAILED, APPLIED,
    ASSERTION, OUTSTANDING, UNBUILT, REVIEW_VERDICT, REVIEW_SUSPECTED, BLOCKED, STOPPED,
    CALL_STARTED, COST, ERROR, CHANGE_SET, ROUTED,
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
