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

`trap_proposed`, `trap_approved` and `trap_declined` are `core/traps.py`'s
`TrapStore` talking, carrying the proposal's `id`, `description`, `phase` and
(on `trap_proposed` only) `evidence`: a proposal a human never acts on is
still a proposal, so it leaves `trap_proposed` on its own, and the approval or
decline that eventually resolves it is a second, separate event rather than a
status rewritten in place, the same way `plan` and `plan_revised` are two
events rather than one plan mutated. A `TrapStore` built with no stream, the
way FCC-184's own tests build it, emits nothing: the stream is optional, not
required.

`consult` and `consult_response` are the one edge from Execute to Plan
(FCC-168, FCC-188): a Preflight failure with attempts still to spend, and the
Step's `promise` handed to Plan unchanged along with `failure_reason`, so the
advice that comes back on `consult_response` is judged the same way a plain
retry already is — Preflight measures the same `promise` next attempt, so
advice that changes it fails there and the existing hand-back edge fires,
never a second gate of its own. Neither `tool_call` nor `thought` carries
`step_number`/`promise`/`failure_reason` as a matter of shape, which is why
this is two kinds of its own rather than a repurposing of either.

`writing` is the reply forming, chunk by chunk, the way `thought` is the
reasoning before it. It is a kind of its own because the two are read
differently — reasoning is the model deciding and may be withheld by the
route, the reply is what the phase will be held to — and because the wait it
accounts for is the larger one: 22.4 minutes of a recorded run's 23.6 minutes
of provider time fell after the last `thought` of every call.
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
BLOCKED = "blocked"
CALL_STARTED = "call_started"
COST = "cost"
ERROR = "error"
TRAP_PROPOSED = "trap_proposed"
TRAP_APPROVED = "trap_approved"
TRAP_DECLINED = "trap_declined"
CONSULT = "consult"
CONSULT_RESPONSE = "consult_response"

KINDS = (
    INTENT, THOUGHT, WRITING, QUESTION, ANSWER, ATTACHED, DESIGN, PART, PLAN, PLAN_REVISED,
    GATE, STEP_STARTED, TOOL_CALL, TOOL_RESULT, PREFLIGHT_FAILED, APPLIED,
    ASSERTION, OUTSTANDING, UNBUILT, REVIEW_VERDICT, BLOCKED, CALL_STARTED, COST,
    ERROR, TRAP_PROPOSED, TRAP_APPROVED, TRAP_DECLINED, CONSULT, CONSULT_RESPONSE,
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
