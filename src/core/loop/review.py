"""The Review phase: judge outcomes and decide next action."""
import json
import tempfile

from core.agent.agent import run_phase
from core.agent import phase_contract as pc
from core.agent.subagent import SessionTreeSupervisor
from core.loop.budgets import PhaseError
from core.loop.execute import _VIEW_NAMES
from core.loop.prompt import REVIEW_PHASE, REVIEW_SUSPECT_PHASE, _as_image, prompt


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

    Not given the Plan, the code, or Execute's reasoning — an independent
    judgement (ADR-0015). This is the run's one gating verdict.
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
