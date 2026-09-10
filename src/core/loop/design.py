"""The Design phase: describe the part from attachments."""
import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor

from core.design.core import A_CHECK_HAZARD
from core.design.merge import merge
from core.events import ERROR
from core.llm import ProviderRefused, StreamCutOff, cache_control_supported
from core.sheets import sheet_reader

from core.loop.budgets import PhaseError
from core.loop.prompt import (
    DESIGN_PHASE,
    _CHARS_PER_TOKEN,
    _ask,
    _pieces_of,
    _the_evidence,
    prompt,
)
from core.loop.results import Stopped

import tempfile
from core.agent.agent import run_phase
from core.agent import phase_contract as pc
from core.agent.subagent import SessionTreeSupervisor


def design(body, llm, events, attachments=(), views=None, phase=DESIGN_PHASE,
          keep_printed=False, pictures=True, supervisor=None, kernel=None):
    """
    One Design turn: what it adds to the part, and what it needs a person for.

    Merge is the only way the description grows, so there is never an answer
    standing beside it to be adjudicated (ADR-0019).

    A context of its own, with nothing to build (ADR-0018): Design and Plan
    used to be one call, and the second pressure spoilt the first — a value
    was invented not because the model saw badly but because it needed
    something to plan with.
    """
    evidence = _the_evidence(attachments, views, keep_printed, pictures)
    if evidence and cache_control_supported(getattr(llm, "model", None)):
        evidence = evidence[:-1] + [dict(evidence[-1], cache_control={"type": "ephemeral"})]

    task = evidence + [{"type": "text", "text": json.dumps(body, indent=2, default=str)}]

    supervisor = supervisor or SessionTreeSupervisor()

    with tempfile.TemporaryDirectory() as working_dir:
        intent_data = body.get("intent", body)

        declared, session, declarations = run_phase(
            pc.DESIGN, working_dir,
            {"intent": intent_data},
            llm, task,
            kernel=kernel, supervisor=supervisor, stream=events,
            system=prompt(DESIGN_PHASE)
        )

    reply = declared if isinstance(declared, dict) else {}
    the_part = reply.get("design")
    return (the_part if isinstance(the_part, dict) else {},
            str(reply.get("question") or "").strip())


_PIXELS_PER_VISUAL_TOKEN = 750


_A_GROUP_BUDGET = 34000


_A_GROUP_CONCURRENCY = 4


def _group_tokens(sheet, views, prompt_tokens, _visual_tokens=None):
    """
    A page-sized turn's own input tokens, estimated the way
    `tests/test_design_payload.py` prices a whole one: text at four characters
    to the token, a picture at its pixel count over 750 — the same divisor a
    provider is measured charging one at (`tests/test_design_payload.py`).

    Read off `_pieces_of` rather than off `_the_evidence`'s finished blocks: a
    picture is still a path here, not a base64 string decoded a second time
    just to be measured.

    `_visual_tokens` is `_split_to_budget`'s per-file cache, threaded down its
    recursion: the same picture sits in more than one candidate group as the
    split narrows, and without it `Image.open` reruns once per recursion
    level a view survives into rather than once per file.
    """
    chars = sum(len(piece["say"]) for piece in _pieces_of(sheet, views) if "say" in piece)
    if sheet.printed:
        chars += len(sheet_reader._the_printed_list(sheet))
    from PIL import Image
    visual = 0.0
    for piece in _pieces_of(sheet, views):
        if "show" not in piece or not os.path.isfile(piece["show"]):
            continue
        path = piece["show"]
        if _visual_tokens is not None and path in _visual_tokens:
            visual += _visual_tokens[path]
            continue
        with Image.open(path) as picture:
            tokens = picture.width * picture.height / _PIXELS_PER_VISUAL_TOKEN
        if _visual_tokens is not None:
            _visual_tokens[path] = tokens
        visual += tokens
    return prompt_tokens + chars // _CHARS_PER_TOKEN + round(visual)


def _split_to_budget(sheet, views, prompt_tokens, _visual_tokens=None):
    """
    *views* as one group, or as two smaller ones split at the middle and
    asked the same question, recursively, until every group prices at or
    under `_A_GROUP_BUDGET`.

    `_A_GROUP_BUDGET` is 34,000: measured against the two Model Mania sheets a
    page prices at 23,829-28,352 tokens (`tests/test_design_payload.py`), so
    the budget clears both with room before a group would grow back toward
    what the whole sheet used to cost in one call.
    """
    if _visual_tokens is None:
        _visual_tokens = {}
    if len(views) <= 1 or _group_tokens(sheet, views, prompt_tokens, _visual_tokens) <= _A_GROUP_BUDGET:
        return [views]
    middle = len(views) // 2
    return (_split_to_budget(sheet, views[:middle], prompt_tokens, _visual_tokens)
           + _split_to_budget(sheet, views[middle:], prompt_tokens, _visual_tokens))


def _the_groups(attachments):
    """
    Every attached, readable sheet as its own page-sized Design calls: one
    group of view names per page, split further only where `_split_to_budget`
    says a page outgrew the budget on its own.

    A page rather than an arbitrary N of views is the boundary, because a page
    is one a reader recognises and a run's log can point at ("design: page
    2") without translating a group id back into what it covers.

    Empty for a run with nothing `sheet_reader` can read — no attachments, a
    text-only Intent, a format neither PyMuPDF nor Pillow knows — which is
    `_first_pass`'s own sign to fall back to today's single whole-attachment
    call rather than page a sheet that was never cut into pages.
    """
    prompt_tokens = len(prompt(DESIGN_PHASE)) // _CHARS_PER_TOKEN
    groups = []
    for path in attachments:
        sheet = sheet_reader.read(path)
        if sheet is None:
            continue
        pages = sorted({crop.page for crop in sheet.crops} | {whole.page for whole in sheet.wholes})
        for page in pages:
            views = sorted({crop.name.split(", part ")[0] for crop in sheet.crops
                            if crop.page == page}
                           | {whole.name for whole in sheet.wholes if whole.page == page})
            if not views:
                continue
            parts = _split_to_budget(sheet, views, prompt_tokens)
            for index, part in enumerate(parts, start=1):
                label = ("page %d" % page if len(parts) == 1 else
                        "page %d, part %d of %d" % (page, index, len(parts)))
                groups.append((label, part))
    return groups


def _asked_about_a_group(label, views, intent, llm, events, attachments):
    """One page-sized group's Design turn — the printed-callout list rides
    along even though `views` narrows the pictures, because a callout printed
    once can explain a feature in a view this group never sees."""
    return design({"intent": intent}, llm, events, attachments, views=views,
                  phase="%s: %s" % (DESIGN_PHASE, label), keep_printed=True)


def _first_pass(intent, llm, events, attachments, cancelled):
    """
    The Design phase's opening move: one call per page-sized group of views,
    run in parallel, each merged into the same part as it lands (ADR-0019 —
    grow-only and order-independent is what makes concurrent merges legitimate).

    A group's failure lands on the part as a `conflicts` entry with
    `kind=A_CHECK_HAZARD`. At most one question crosses the fan-out, the
    first a group asked in page order: the phase still asks once (ADR-0022).
    """
    groups = _the_groups(attachments)
    if not groups:
        the_part, question = design({"intent": intent}, llm, events, attachments)
        return the_part, question, False

    if cancelled():
        raise Stopped()

    the_part = {}
    question = ""
    with ThreadPoolExecutor(max_workers=min(len(groups), _A_GROUP_CONCURRENCY)) as pool:
        futures = [(label, pool.submit(_asked_about_a_group, label, views, intent,
                                       copy.copy(llm), events, attachments))
                  for label, views in groups]
        for label, future in futures:
            try:
                grown, asked = future.result()
            except (PhaseError, ProviderRefused, StreamCutOff) as failed:
                the_part = merge(the_part, {"conflicts": [
                    {"about": "the %s of this drawing" % label,
                     "stands": "Design could not be asked about it: %s" % failed,
                     "kind": A_CHECK_HAZARD,
                     "machine_written": True}]})
                events.emit(ERROR, message="Design could not read the %s: %s"
                                          % (label, failed))
                continue
            the_part = merge(the_part, grown)
            if asked and not question:
                question = asked
    return the_part, question, True


def _reconciled(the_part, intent, llm, events, attachments):
    """
    One turn over what the page-sized calls produced, seeing no pictures —
    every named list `shown()` would draw one beside, as `_words_only` gives
    it, instead. This is the half of cross-view reading a page cannot do for
    itself: a page-sized call sees its own view's line work and the sheet's
    printed callouts, and this is the turn that sees every page's words at
    once and settles what only the whole sheet can.
    """
    return design({"intent": intent, "the_part_as_you_described_it": the_part},
                  llm, events, attachments, pictures=False)
