"""
What an event says to a person, said once here rather than in the Qt panel's
and `run.py`'s own separate chains, which drifted apart until one silently
stopped narrating several kinds -- nothing failed, because prose inside a
`QDockWidget` has nothing to test it. What is left to each narrator is where
a line goes -- a chat bubble, a collapsible row, a status line, stdout --
and the shapes that are not prose at all: the Steps of a Plan, a block of
code, a picture. The Plan Gate's three answers are written out here too, in
`core.loop`'s own words, because importing the loop in order to narrate it
would point the dependency the wrong way.
"""
from dataclasses import dataclass

from core.agent.domain import label
from core.events import (
    ABORTED, ANSWER, APPLIED, ASSERTION, ATTACHED, BLOCKED, BUDGET_EXHAUSTED,
    COMPLETED, DISPUTED, DOMAIN, ERROR, FAILED, GATE, INTENT, OUTSTANDING, PART, PLAN_REVISED,
    PREFLIGHT_FAILED, QUESTION, REVIEW_SUSPECTED, REVIEW_VERDICT, ROUTED,
    STEP_DECLARED, STEP_STARTED, STOPPED, TOOL_RESULT, TURN_BUDGET_EXHAUSTED, UNBUILT,
    WARNING,
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
    One event, in words: `line` is the sentence that belongs in the
    transcript, `title` and `body` are the detail that folds away under it,
    and `status` is the one-line progress note that replaces itself, so an
    event carrying only a status says nothing permanent. A `blocked` event
    needs both a line and a folded body, so the title is a field rather than
    the line doing double duty. All empty means the event is not prose: a
    Plan is its Steps, a tool call is its code, an attachment is its
    picture, reasoning is a stream rather than a sentence, and the
    description is a file, said by the path the part event carries.
    """

    line: str = ""
    title: str = ""
    body: str = ""
    status: str = ""


_NOTHING = Narration()


def narrate(event) -> Narration:
    """
    What *event* says; never raises on an unknown kind, it just says
    nothing. Several kinds say less than they carry: the user's own answer
    is already in the transcript, so only the fact that nobody answered, or
    didn't need to, is worth saying, and a description is said by its path
    rather than distilled, because a document is corrected by opening it,
    not read in a transcript. The Gate is a status rather than a line since
    the panel already puts the question in its own card, except a standing
    approval, which says so rather than looking like a skipped Gate; a Step
    not built says what was not reached, because a blocked run and an
    unmet-entry run are different things the transcript must not spell the
    same.
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

    if kind == ROUTED:
        return Narration(status="Routed to: %s" % data.get("route", ""))

    if kind == DOMAIN:
        return Narration(line="Domain: %s%s" % (label(data["domain"]),
                                                " (inferred)" if data.get("inferred") else ""))

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
        result = data["result"]
        status = "%s → %s" % (data["name"], result.get("status", "done"))
        printed, raised = result.get("stdout") or "", result.get("traceback") or ""
        if not (printed or raised):
            return Narration(status=status)
        return Narration(status=status,
                         title="✗ %s raised" % data["name"] if raised
                               else "%s printed" % data["name"],
                         body="\n".join(said for said in (printed, raised) if said))

    if kind == PREFLIGHT_FAILED:
        return Narration(title="✗ Preflight failed (attempt %d) — retrying" % data["attempt"],
                         body=data["traceback"])

    if kind == APPLIED:
        created = ", ".join(data.get("created") or []) or "no new objects"
        return Narration(line="✓ Applied “%s” — %s. Ctrl+Z undoes this Step."
                              % (data["label"], created))

    if kind == STEP_DECLARED:
        return Narration(line="✓ Declared Step %s “%s”." % (data["step"], data.get("label") or ""))

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

    if kind == REVIEW_SUSPECTED:
        entries = data.get("suspected") or []
        if not entries:
            return Narration(line="Review raised nothing to check further before Assertion.")
        return Narration(
            line="Review named %d thing%s to check further before judging the result."
                 % (len(entries), "" if len(entries) == 1 else "s"),
            title="What Review suspected (%d)" % len(entries),
            body="\n".join("• " + str(entry) for entry in entries))

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

    if kind == DISPUTED:
        return Narration(line="⚖  Disputed, with a measurement: " + "; ".join(data["entries"]),
                         title="What the part measures (%d)" % len(data["entries"]),
                         body="\n".join("• %s — %s (%s)" % item for item in
                                        zip(data["entries"], data["measured"], data["because"])))

    if kind == STOPPED:
        return Narration(line="⏸  " + data["reason"])

    if kind == ABORTED:
        return Narration(line="⏹  " + data["reason"])

    if kind == BUDGET_EXHAUSTED:
        tried = data.get("tried") or []
        if not tried:
            return Narration(line="⏸  " + data["reason"])
        return Narration(line="⏸  " + data["reason"],
                         title="What was tried (%d)" % len(tried),
                         body=_what_was_tried(tried))

    if kind == TURN_BUDGET_EXHAUSTED:
        return Narration(line="⏸  %s's turn budget (%d) was reached at turn %d."
                         % (data["phase"], data["budget"], data["turns"]))

    if kind == FAILED:
        return Narration(title="✗ Something went wrong", body=data["message"])

    if kind == COMPLETED:
        return Narration(line="✓ Done.")

    if kind == ERROR:
        return Narration(title="✗ Something went wrong", body=data["message"])

    if kind == WARNING:
        return Narration(line="⚠  %s" % data["message"])

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
