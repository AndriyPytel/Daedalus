"""
What an event says to a person.

The stream is narrated in two places — the Qt panel and `tools/run_slice.py` —
and they used to be two `if/elif` chains over the same sixteen kinds. The second
one drifted: it still read the description off the `plan` event months after
ADR-0018 gave it an event of its own, so the slice runner silently stopped
narrating it and never learned `attached`, `assertion`, `question` or `answer`
either. Nothing failed, because prose that only exists inside a
`QDockWidget` has nothing to test it.

So the wording lives here, once, with no Qt in sight. What is left to each
narrator is where a line goes — a chat bubble, a collapsible row, a status
line, stdout — and the shapes that are not prose at all: the Steps of a Plan,
a block of code, a picture.

The Plan Gate's three answers are written out here as well. The words are
`core.loop`'s, but importing the loop in order to narrate it would point the
dependency the wrong way.
"""
from dataclasses import dataclass

from core.events import (
    ANSWER, APPLIED, ASSERTION, ATTACHED, BLOCKED, ERROR, GATE, INTENT,
    OUTSTANDING, PART, PLAN_REVISED, PREFLIGHT_FAILED, QUESTION, REVIEW_VERDICT,
    STEP_STARTED, TOOL_RESULT, UNBUILT,
)

_GATE_SAID = {
    None: "Waiting for you to approve the Plan…",
    "approve": "Approved — running the Plan.",
    "amend": "Amending the Plan…",
    "reject": "Rejected.",
}


@dataclass(frozen=True)
class Narration:
    """
    One event, in words.

    `line` is the sentence that belongs in the transcript. `title` and `body`
    are the detail that folds away under it — a traceback, what the last run
    left wrong, the attempts behind a block. `status` is the one-line progress
    note that replaces itself; an event carrying only a status says nothing
    permanent.

    A `blocked` event is the one that needs both a line and a folded body, so
    the title is a field rather than the line doing double duty.

    All empty means the event is not prose: a Plan is its Steps, a tool call is
    its code, an attachment is its picture, reasoning is a stream rather than a
    sentence, and the description is a file, said by the path the part event
    carries.
    """

    line: str = ""
    title: str = ""
    body: str = ""
    status: str = ""


_NOTHING = Narration()


def narrate(event) -> Narration:
    """
    What *event* says. Never raises on an unknown kind — it just says nothing.

    Several kinds say less than they carry. The user's own answer is already in
    the transcript, so what is worth saying is that nobody gave one — or that
    nobody had to, because the drawing answered it. The description is not said
    at all: it is a document, and what a document gets is a path. A part is
    given by that path, because the file is the point — a description long
    enough to be worth distilling is not read in a transcript wherever it is
    put, it is corrected by opening the file the run is planned from, and one
    run is compared with the last by diffing two of them. The Gate is a
    status rather than a line, since the panel already puts the question in a
    card of its own and the run is only waiting; except where nobody was asked,
    because a standing approval is a decision the user made once and is having
    applied again, and it says so rather than looking like a Gate that was
    skipped. A revision names which one it is of how many and what caused it,
    because the reason alone never said that the same wall had been hit three
    times. The acceptance checks get a line of their own and never the Review
    verdict's, because the checks are code and Review deliberately never sees
    code. A Step that was not built says what was not reached and that the run
    carried on regardless, because a blocked run and a run with an unmet entry
    are different things and the transcript must not spell them the same.
    """
    data = event.data
    kind = event.kind

    if kind == INTENT:
        return Narration(line="What was asked for: %s" % data.get("text", ""))

    if kind == QUESTION:
        return Narration(line=data["text"])

    if kind == ANSWER:
        by = data.get("by")
        if by == "drawing":
            return Narration(line="Looked at the drawing again — " + data["text"])
        if by != "user":
            return Narration(line="No values given, so the Plan will say what it assumed.")
        return _NOTHING

    if kind == ATTACHED:
        names = [file["name"] for file in data.get("files") or []]
        return Narration(line="Attached: " + ", ".join(names)) if names else _NOTHING

    if kind == PART:
        return Narration(line="The part as read: %s" % data.get("path", ""))

    if kind == GATE:
        if data.get("standing") and data.get("decision"):
            return Narration(status="Approved already — carrying on without asking.")
        return Narration(status=_GATE_SAID.get(data.get("decision"),
                                               "The Plan Gate answered: %s"
                                               % data.get("decision")))

    if kind == OUTSTANDING:
        entries = data.get("entries") or []
        return Narration(
            line="⚠ The last run left %d thing%s wrong with this Document, and this run "
                 "is told to put %s right." % (len(entries), "" if len(entries) == 1 else "s",
                                               "it" if len(entries) == 1 else "them"),
            title="What was still wrong (%d)" % len(entries),
            body="\n".join("• " + str(entry) for entry in entries))

    if kind == PLAN_REVISED:
        counted = (" (%d of %d, %s)" % (data["revision"], data["of"], data["because"])
                   if data.get("of") else "")
        return Narration(line="Revised the Plan%s: %s" % (counted, data["reason"]))

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
        return Narration(line=_assertion_said(data))

    if kind == UNBUILT:
        entries = data.get("entries") or []
        said = ("⚠ Step %d/%d was not built: %s — %s"
                % (data["step"], data["total"], data["description"], data["why"]))
        if entries:
            said += "\nNot reached: " + ", ".join(str(entry) for entry in entries)
        return Narration(line=said + "\nNothing in the geometry was waiting on it, so the "
                                     "run carried on.")

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
