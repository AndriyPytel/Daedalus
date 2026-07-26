"""
The typed event stream.

The loop emits; the panel renders. The panel never calls loop methods, so the
loop has no idea a UI exists and the same stream doubles as the session log —
a bad run can be inspected without reproducing it.

The stream is an output. It is asserted on and never substituted.
"""
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

THOUGHT = "thought"
PLAN = "plan"
PLAN_REVISED = "plan_revised"
STEP_STARTED = "step_started"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
PREFLIGHT_FAILED = "preflight_failed"
RENDER = "render"
APPLIED = "applied"
REVIEW_VERDICT = "review_verdict"
BLOCKED = "blocked"
ERROR = "error"

KINDS = (
    THOUGHT, PLAN, PLAN_REVISED, STEP_STARTED, TOOL_CALL, TOOL_RESULT,
    PREFLIGHT_FAILED, RENDER, APPLIED, REVIEW_VERDICT, BLOCKED, ERROR,
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
        if kind not in KINDS:
            raise ValueError("unknown event kind: %r" % kind)
        event = Event(kind=kind, data=data)
        self._events.append(event)
        for subscriber in self._subscribers:
            # A broken renderer must not take the run down with it.
            try:
                subscriber(event)
            except Exception:
                pass
        return event

    # ── reading ──────────────────────────────────────────────────────────────

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

        for event in self._events:      # anything already emitted
            write(event)
        self.subscribe(write)
        return handle
