"""
What an event says to a person.

The stream is narrated in two places — the Qt panel and `tools/run_slice.py` —
and they used to be two `if/elif` chains over the same sixteen kinds. The second
one drifted: it still read the Reading off the `plan` event months after
ADR-0018 gave the Reading an event of its own, so the slice runner silently
stopped narrating it and never learned `attached`, `assertion`, `question` or
`answer` either. Nothing failed, because prose that only exists inside a
`QDockWidget` has nothing to test it.

So the wording lives here, once, with no Qt in sight. What is left to each
narrator is where a line goes — a chat bubble, a collapsible row, a status
line, stdout — and the shapes that are not prose at all: the Steps of a Plan,
a block of code, a picture.
"""
from dataclasses import dataclass

from core.events import (
    ANSWER, APPLIED, ASSERTION, ATTACHED, BLOCKED, ERROR, PLAN_REVISED,
    PREFLIGHT_FAILED, QUESTION, READING, REVIEW_VERDICT, STEP_STARTED,
    TOOL_RESULT,
)


@dataclass(frozen=True)
class Narration:
    """
    One event, in words.

    `line` is the sentence that belongs in the transcript. `title` and `body`
    are the detail that folds away under it — a traceback, a reading, the
    attempts behind a block. `status` is the one-line progress note that
    replaces itself; an event carrying only a status says nothing permanent.

    A `blocked` event is the one that needs both a line and a folded body, so
    the title is a field rather than the line doing double duty.

    All empty means the event is not prose: a Plan is its Steps, a tool call is
    its code, an attachment is its picture, and reasoning is a stream rather
    than a sentence.
    """

    line: str = ""
    title: str = ""
    body: str = ""
    status: str = ""


_NOTHING = Narration()


def narrate(event) -> Narration:
    """What *event* says. Never raises on an unknown kind — it just says nothing."""
    data = event.data
    kind = event.kind

    if kind == QUESTION:
        return Narration(line=data["text"])

    if kind == ANSWER:
        # The user's own answer is already in the transcript; what is worth
        # saying is that nobody gave one (FCC-25) — or that nobody had to,
        # because the drawing answered it (FCC-90).
        by = data.get("by")
        if by == "drawing":
            return Narration(line="Looked at the drawing again — " + data["text"])
        if by != "user":
            return Narration(line="No values given, so the Plan will say what it assumed.")
        return _NOTHING

    if kind == ATTACHED:
        names = [file["name"] for file in data.get("files") or []]
        return Narration(line="Attached: " + ", ".join(names)) if names else _NOTHING

    if kind == READING:
        return Narration(title="How the drawing was read", body=data["text"])

    if kind == PLAN_REVISED:
        return Narration(line="Revised the Plan: " + data["reason"])

    if kind == STEP_STARTED:
        return Narration(status="Step %d/%d: %s"
                                % (data["index"], data["total"], data["description"]))

    if kind == TOOL_RESULT:
        return Narration(status="%s → %s" % (data["name"],
                                             data["result"].get("status", "done")))

    if kind == PREFLIGHT_FAILED:
        return Narration(title="✗ Preflight failed (attempt %d) — retrying" % data["attempt"],
                         body=data["traceback"])

    if kind == APPLIED:
        created = ", ".join(data.get("created") or []) or "no new objects"
        return Narration(line="✓ Applied “%s” — %s. Ctrl+Z undoes this Step."
                              % (data["label"], created))

    if kind == ASSERTION:
        # Its own line, never REVIEW_VERDICT's: the checks are code, and Review
        # deliberately never sees code (ADR-0003).
        return Narration(line=_assertion_said(data))

    if kind == REVIEW_VERDICT:
        return Narration(line="%s Review: %s — %s"
                              % ("✓" if data["verdict"] == "pass" else "✗",
                                 data["verdict"], data["reason"]))

    if kind == BLOCKED:
        tried = data.get("tried") or []
        if not tried:
            return Narration(line="⏸  " + data["reason"])
        return Narration(line="⏸  " + data["reason"],
                         title="What was tried (%d)" % len(tried),
                         body=_what_was_tried(tried))

    if kind == ERROR:
        return Narration(title="✗ Something went wrong", body=data["message"])

    return _NOTHING


def _assertion_said(data) -> str:
    """
    What the acceptance checks came to, in one line.

    Three outcomes, not two: `ok` is None when the checks could not be written
    at all, which is a fact about the checking code and not a verdict on the
    part.
    """
    if data.get("ok") is None:
        return "⚠ Acceptance checks could not be written; Review judges the list alone."
    if not data.get("ok"):
        return "✗ Acceptance checks failed:\n" + (data.get("traceback") or "")

    said = "✓ Acceptance checks passed: %d confirmed." % len(data.get("confirmed") or [])
    uncovered = data.get("uncovered") or []
    if uncovered:
        said += " Left to Review: " + ", ".join(uncovered) + "."
    return said


def _what_was_tried(tried) -> str:
    """Every attempt behind a block, each closed off by a rule."""
    written = []
    for entry in tried:
        written.append(str(entry.get("code") or entry.get("review") or entry))
        if entry.get("traceback"):
            written.append("\n" + entry["traceback"])
        written.append("\n" + "─" * 40 + "\n")
    return "".join(written)
