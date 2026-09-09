"""The Review phase: judge outcomes and decide next action."""
import json
import tempfile

from core.agent.agent import run_phase
from core.agent import phase_contract as pc
from core.agent.subagent import SessionTreeSupervisor
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


def review_suspect(intent, views, described, llm, events, attachments=(),
                   supervisor=None, kernel=None):
    """
    Look at the finished Document before Assertion measures it, and name what
    a render alone can only suspect. Never a verdict of its own (ADR-0015).
    """
    request = {"intent": intent, "the_document_as_it_now_stands": described}
    content = _review_content(request, views, attachments)
    
    supervisor = supervisor or SessionTreeSupervisor()
    task = content
    
    with tempfile.TemporaryDirectory() as working_dir:
        declared, session, checkpoints = run_phase(
            pc.REVIEW_SUSPECT, working_dir,
            {"description": described},
            llm, task,
            kernel=kernel, supervisor=supervisor, stream=events,
            system=prompt(REVIEW_SUSPECT_PHASE)
        )
    
    reply = declared if isinstance(declared, dict) else {}
    suspected = [str(entry).strip() for entry in (reply.get("suspected") or ())
                if str(entry).strip()]
    return [entry + " (suspected)" for entry in suspected]


def review(intent, views, described, llm, events, attachments=(), checked=None,
           supervisor=None, kernel=None):
    """
    Judge the outcome against the Intent.

    Given the Intent, the views, the Description of the finished Document, and
    whatever the user showed with the Intent. Deliberately not given the Plan,
    the code, or Execute's reasoning — an independent judgement of how it
    turned out, not of how it was produced.

    One view is not enough — a mirrored tooth flank reads only from above, and
    in isometry it is foreshortened, which is why Review passed one. Neither is
    the picture enough on its own: a hole is a dimension, not a pixel, and the
    Description is where it can be read.

    Without the attachments this compared a render against a sentence, so "does
    this resemble what I was shown" was not a question it could ask.

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
    
    supervisor = supervisor or SessionTreeSupervisor()
    task = content
    
    with tempfile.TemporaryDirectory() as working_dir:
        declared, session, checkpoints = run_phase(
            pc.REVIEW, working_dir,
            {"description": described},
            llm, task,
            kernel=kernel, supervisor=supervisor, stream=events,
            system=prompt(REVIEW_PHASE)
        )
    
    reply = declared if isinstance(declared, dict) else {}
    verdict = str(reply.get("verdict", "")).lower()
    if verdict not in ("pass", "fail"):
        raise PhaseError("Review replied with an unusable verdict: %r" % reply.get("verdict"))
    return verdict, str(reply.get("reason", ""))
