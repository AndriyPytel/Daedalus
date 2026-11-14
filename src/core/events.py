"""
The typed event stream: the loop emits, the panel renders, and a run can be
inspected from this stream alone without reproducing it, with the loop
itself asserting on it and never substituting for it. A run ends one of four
ways that a rate over the stream may never merge: `completed`, `blocked`,
`budget_exhausted`, or `failed`. Measured: 22.4 of a recorded run's 23.6
minutes of provider time fell after the last `thought` of every call, so most
of the wait is the reply, not the reasoning; `cost.usd` is present only when
the client reported a price, and `cost.usage_unavailable` marks a truncated
call whose provider reported no usage at all.
"""
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.agent.domain import label

INTENT = "intent"
THOUGHT = "thought"
WRITING = "writing"
QUESTION = "question"
ANSWER = "answer"
ATTACHED = "attached"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
PREFLIGHT_FAILED = "preflight_failed"
APPLIED = "applied"
ERROR = "error"
WARNING = "warning"
CALL_STARTED = "call_started"
COST = "cost"
ROUTED = "routed"
DOMAIN = "domain"
COMPLETED = "completed"
BUDGET_EXHAUSTED = "budget_exhausted"
FAILED = "failed"
INVOKE_MARKUP = "invoke_markup"

ASSERTION = "assertion"
BLOCKED = "blocked"
DESIGN = "design"
DISPUTED = "disputed"
GATE = "gate"
PLAN_REVISED = "plan_revised"
REVIEW_VERDICT = "review_verdict"
STEP_DECLARED = "step_declared"
STEP_STARTED = "step_started"


@dataclass(frozen=True)
class Narration:
    """
    One event, in words: `line` is the sentence that belongs in the
    transcript, `title` and `body` are the detail that folds away under it,
    and `status` is the one-line progress note that replaces itself, so an
    event carrying only a status says nothing permanent. All empty means the
    event is not prose: a tool call is its code, an attachment is its
    picture, and reasoning is a stream rather than a sentence.
    """

    line: str = ""
    title: str = ""
    body: str = ""
    status: str = ""


@dataclass(frozen=True)
class Kind:
    """
    One kind of event: the `keys` its data carries, and how it is said.

    `narration` turns the data into a `Narration`; a kind with none is
    `structural`, and the renderer draws it or leaves it in the log.
    """

    name: str
    keys: Tuple[str, ...]
    narration: Optional[Callable[[Dict[str, Any]], Narration]] = None
    structural: bool = False


def _asked(data):
    return Narration(line="What was asked for: %s" % data.get("text", ""))


def _question(data):
    return Narration(line=data["text"])


def _answer(data):
    by = data.get("by")
    if by == "drawing":
        return Narration(line="Looked at the drawing again — " + data["text"])
    if by != "user":
        return Narration(line="No values given, so the Plan will say what it assumed.")
    return Narration()


def _attached(data):
    names = [file["name"] for file in data.get("files") or []]
    return Narration(line="Attached: " + ", ".join(names)) if names else Narration()


def _routed(data):
    return Narration(status="Routed to: %s" % data.get("route", ""))


def _domain(data):
    return Narration(line="Domain: %s%s" % (label(data["domain"]),
                                            " (inferred)" if data.get("inferred") else ""))


def _tool_result(data):
    result = data["result"]
    status = "%s → %s" % (data["name"], result.get("status", "done"))
    printed, raised = result.get("stdout") or "", result.get("traceback") or ""
    if not (printed or raised):
        return Narration(status=status)
    return Narration(status=status,
                     title="✗ %s raised" % data["name"] if raised
                           else "%s printed" % data["name"],
                     body="\n".join(said for said in (printed, raised) if said))


def _preflight_failed(data):
    return Narration(title="✗ Preflight failed (attempt %d) — retrying" % data["attempt"],
                     body=data["traceback"])


def _applied(data):
    created = ", ".join(data.get("created") or []) or "no new objects"
    return Narration(line="✓ Applied “%s” — %s. Ctrl+Z undoes this Step."
                          % (data["label"], created))


def _budget_exhausted(data):
    tried = data.get("tried") or []
    if not tried:
        return Narration(line="⏸  " + data["reason"])
    return Narration(line="⏸  " + data["reason"],
                     title="What was tried (%d)" % len(tried),
                     body=_what_was_tried(tried))


def _what_was_tried(tried):
    written = []
    for entry in tried:
        written.append(str(entry.get("code") or entry.get("review") or entry))
        if entry.get("traceback"):
            written.append("\n" + entry["traceback"])
        written.append("\n" + "─" * 40 + "\n")
    return "".join(written)


def _went_wrong(data):
    return Narration(title="✗ Something went wrong", body=data["message"])


def _warned(data):
    return Narration(line="⚠  %s" % data["message"])


def _done(data):
    return Narration(line="✓ Done.")


REGISTRY: Dict[str, Kind] = {kind.name: kind for kind in (
    Kind(INTENT, ("text",), _asked),
    Kind(THOUGHT, ("phase", "text"), structural=True),
    Kind(WRITING, ("phase", "text"), structural=True),
    Kind(QUESTION, ("text",), _question),
    Kind(ANSWER, ("text", "by"), _answer),
    Kind(ATTACHED, ("files",), _attached),
    Kind(TOOL_CALL, ("name", "arguments"), structural=True),
    Kind(TOOL_RESULT, ("name", "result"), _tool_result),
    Kind(PREFLIGHT_FAILED, ("step", "attempt", "traceback", "gate"), _preflight_failed),
    Kind(APPLIED, ("step", "label", "created"), _applied),
    Kind(ERROR, ("message",), _went_wrong),
    Kind(WARNING, ("message",), _warned),
    Kind(CALL_STARTED, ("phase", "model", "ceiling", "effort"), structural=True),
    Kind(COST, ("phase", "seconds", "model", "usd", "prompt_tokens", "completion_tokens",
                "cached_tokens", "cache_creation_tokens", "reasoning_tokens",
                "usage_unavailable", "resolved_model", "stop_reason", "duration_api_ms",
                "num_turns", "api_error_status", "terminal_reason"), structural=True),
    Kind(ROUTED, ("route",), _routed),
    Kind(DOMAIN, ("domain", "inferred"), _domain),
    Kind(COMPLETED, ("summary",), _done),
    Kind(BUDGET_EXHAUSTED, ("reason", "tried"), _budget_exhausted),
    Kind(FAILED, ("message",), _went_wrong),
    Kind(INVOKE_MARKUP, ("phase", "name", "parameters", "recovered"), structural=True),
)}

KINDS = tuple(REGISTRY)


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
                pass  # except-pass-allowed: one subscriber's failure must not break the others
        return event

    def __iter__(self):
        return iter(tuple(self._events))

    def __len__(self):
        return len(self._events)

    def last(self, kind: str):
        for event in reversed(self._events):
            if event.kind == kind:
                return event
        return None

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
