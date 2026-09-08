"""The Review phase: judge outcomes and decide next action."""
import json

from core.loop.budgets import PhaseError
from core.loop.execute import _VIEW_NAMES
from core.loop.prompt import (
    REVIEW_PHASE,
    REVIEW_SUSPECT_PHASE,
    _as_image,
    _ask,
    prompt,
)


def _review_content(request, views, attachments):
    """
    A Review request's content blocks — the views, named for the prose, then
    whatever the user attached to the Intent.

    Both Review calls send this shape; only what they put in `request` before
    calling this differs, and neither is told how the result was produced
    (ADR-0003).
    """
    content = [{"type": "text", "text": json.dumps(request, indent=2, default=str)}]

    pictures = [picture for picture in map(_as_image, views or ()) if picture]
    if len(pictures) > 1:
        content[0]["text"] += (
            "\n\nThe next %d images are the same result seen from %s. They are one part, "
            "not several — a defect that reads in any one of them is a defect."
            % (len(pictures), ", ".join(_VIEW_NAMES[:len(pictures)])))
    if pictures:
        content.extend(pictures)
    else:
        content[0]["text"] += ("\n\nNo render was produced, so judge on the description "
                               "alone.")

    shown = [drawing for drawing in map(_as_image, attachments) if drawing]
    if shown:
        content[0]["text"] += (
            "\n\nThe last %d attachment%s is what the user showed with the Intent, not "
            "the result. Judge whether the result resembles it."
            % (len(shown), "s are" if len(shown) > 1 else " is")
        )
        content.extend(shown)
    return content


def review_suspect(intent, views, described, llm, events, attachments=()):
    """
    Look at the finished Document before Assertion measures it, and name what
    a render alone can only suspect.

    Runs first, on the same views and the same Description Review #2 judges
    later, so what it notices still has Assertion ahead of it to confirm or
    refute — never a verdict of its own (FCC-204: a suspicion that could pass
    or fail the run on its own is the flaw ADR-0015 already ruled out).

    What it returns is zero or more Acceptance entries, each written the way
    every other entry already is — `address: value` — and flagged `suspected`
    here rather than trusted from the reply, the way a view-read entry is
    flagged `derived` by the loop and not by the phase that read it. Assertion
    reads them exactly as it reads the rest of the list (FCC-258).
    """
    request = {"intent": intent, "the_document_as_it_now_stands": described}
    content = _review_content(request, views, attachments)

    reply = _ask(llm, events, REVIEW_SUSPECT_PHASE, prompt(REVIEW_SUSPECT_PHASE), content)

    suspected = [str(entry).strip() for entry in (reply.get("suspected") or ())
                if str(entry).strip()]
    return [entry + " (suspected)" for entry in suspected]


def review(intent, views, described, llm, events, attachments=(), checked=None):
    """
    Judge the outcome against the Intent.

    Given the Intent, the views, the Description of the finished Document, and
    whatever the user showed with the Intent. Deliberately not given the Plan,
    the code, or Execute's reasoning — an independent judgement of how it
    turned out, not of how it was produced.

    One view is not enough — a mirrored tooth flank reads only from above, and
    in isometry it is foreshortened, which is why Review passed one. Neither is
    the picture enough on its own: a hole is a dimension, not a pixel, and the
    Description is where it can be read (FCC-81).

    Without the attachments this compared a render against a sentence, so "does
    this resemble what I was shown" was not a question it could ask (FCC-47).

    The views arrive in the order `capture_review_views` writes them, which is
    what `_VIEW_NAMES` names them by for Review's own prose — the same views
    `review_suspect` was shown, captured once for both (a render does not
    change between them; Assertion does not draw). What the checks confirmed
    and what they could not cover go with them, so the verdict is about what
    the checks did not cover rather than a second opinion on what they did
    (ADR-0015). This is the run's one gating verdict: `review_suspect` never
    supplies one, so `measure.py`'s review_fail_rate stays a measure of this
    call alone.
    """
    request = {"intent": intent, "the_document_as_it_now_stands": described}
    if checked is not None:
        request["facts_the_checks_confirmed"] = list(checked.confirmed)
        request["what_the_checks_could_not_cover"] = list(checked.uncovered)
    content = _review_content(request, views, attachments)

    reply = _ask(llm, events, REVIEW_PHASE, prompt(REVIEW_PHASE), content)

    verdict = str(reply.get("verdict", "")).lower()
    if verdict not in ("pass", "fail"):
        raise PhaseError("Review replied with an unusable verdict: %r" % reply.get("verdict"))
    return verdict, str(reply.get("reason", ""))
