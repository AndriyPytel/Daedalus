"""The Design phase: describe the part from attachments."""
import contextvars
import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor

from core.design.core import A_CHECK_HAZARD
from core.design.merge import merge
from core.events import ERROR
from core.prompts import read_prompt
from core.llm import ProviderRefused, StreamCutOff, cache_control_supported
from core.sheets import sheet_reader

from core.loop.budgets import PhaseError
from core.loop.prompt import (
    DESIGN_PHASE,
    _CHARS_PER_TOKEN,
    _pieces_of,
    _the_evidence,
    a_raster_picture,
    prompt,
)
from core.loop.results import Stopped

from core.agent.agent import run_declaring
from core.agent import phase_contract as pc


def design(body, llm, events, attachments=(), views=None, phase=DESIGN_PHASE,
          keep_printed=False, pictures=True, supervisor=None, kernel=None):
    """
    One Design turn: what it adds to the part, and what it needs a
    person for. Merge is the only way the description grows, so there is
    never an answer standing beside it to be adjudicated. A context of
    its own, with nothing to build: a Plan pressure beside it invents a
    value not because the model saw badly, but because it needed
    something to plan with.
    """
    evidence = _the_evidence(attachments, views, keep_printed, pictures)
    if evidence and cache_control_supported(getattr(llm, "model", None)):
        evidence = evidence[:-1] + [dict(evidence[-1], cache_control={"type": "ephemeral"})]

    task = evidence + [{"type": "text", "text": json.dumps(body, indent=2, default=str)}]

    intent_data = body.get("intent", body)
    reply, _ = run_declaring(
        pc.DESIGN, {"intent": intent_data}, llm, task, kernel=kernel,
        supervisor=supervisor, stream=events, system=prompt(DESIGN_PHASE, raster=a_raster_picture(attachments)))
    the_part = reply.get("design")
    return (the_part if isinstance(the_part, dict) else {},
            str(reply.get("question") or "").strip())


_PIXELS_PER_VISUAL_TOKEN = 750


_A_GROUP_BUDGET = 34000


_A_GROUP_CONCURRENCY = 4


def _group_tokens(sheet, views, prompt_tokens, _visual_tokens=None):
    """
    A page-sized turn's own input tokens: text at four chars to the token, a picture at pixel count over 750, the divisor a provider is measured charging at (`tests/test_design_payload.py`).

    Read off `_pieces_of`, not `_the_evidence`'s finished blocks, so a picture stays a path rather than a decoded base64 string. `_visual_tokens` caches per file across `_split_to_budget`'s recursion.
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
    *views* as one group, or two smaller ones split at the middle and asked
    the same question, recursively, until every group prices at or under
    `_A_GROUP_BUDGET`. `_A_GROUP_BUDGET` is 34,000: the two Model Mania
    sheets price a page at 23,829-28,352 tokens
    (`tests/test_design_payload.py`), clearing both with room to spare.
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
    Every attached, readable sheet as its own page-sized Design calls: one group of view names per page, split further where `_split_to_budget` says a page outgrew the budget.

    A page is the boundary since a run's log can point at "design: page 2" without a group id to translate. Empty where `sheet_reader` reads nothing — `_first_pass`'s sign to fall back to one whole-attachment call.
    """
    prompt_tokens = len(prompt(DESIGN_PHASE, raster=a_raster_picture(attachments))) // _CHARS_PER_TOKEN
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


def _body(intent, base):
    """What Design is asked about: the intent, and the base part's inventory where the run edits one."""
    if not base:
        return {"intent": intent}
    return {"intent": intent, "the_base_part": base, "locate_the_edit": _LOCATE_THE_EDIT,
            "describe": "the part as it is after the intent is applied to the_base_part"}


_LOCATE_THE_EDIT = read_prompt("locate_the_edit.md")


def _asked_about_a_group(label, views, intent, llm, events, attachments, readings=1,
                         record_conflicts=True, base=None):
    """One page-sized group's Design turn, read *readings* times and merged
    into one group-local model — a dimension digit at the provider's pixel
    cap is a stochastic read, so repeating the turn and merging catches a
    misreading as a conflict instead of a single silent value. The
    printed-callout list rides along even though `views` narrows the
    pictures, because a callout printed once can explain a feature in a
    view this group never sees."""
    grown, question = {}, ""
    for _ in range(max(1, readings)):
        this, asked = design(_body(intent, base), llm, events, attachments, views=views,
                             phase="%s: %s" % (DESIGN_PHASE, label), keep_printed=True)
        grown = merge(grown, this, record_conflicts)
        question = question or asked
    return grown, question


def _first_pass(intent, llm, events, attachments, cancelled, readings=1,
                record_conflicts=True, base=None):
    """
    The Design phase's opening move: one call per page-sized group of views,
    each read *readings* times and merged as it lands, since grow-only and
    order-independent merging makes concurrent merges legitimate. A group's
    failure lands as a `conflicts` entry with `kind=A_CHECK_HAZARD`; at most
    one question crosses the fan-out, the first asked in page order.
    """
    groups = _the_groups(attachments)
    if not groups:
        the_part, question = design(_body(intent, base), llm, events, attachments)
        return the_part, question, False

    if cancelled():
        raise Stopped()

    the_part = {}
    question = ""
    with ThreadPoolExecutor(max_workers=min(len(groups), _A_GROUP_CONCURRENCY)) as pool:
        futures = [(label, pool.submit(contextvars.copy_context().run, _asked_about_a_group, label, views, intent,
                                       copy.copy(llm), events, attachments, readings,
                                       record_conflicts, base))
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
            the_part = merge(the_part, grown, record_conflicts)
            if asked and not question:
                question = asked
    return the_part, question, True


def _reconciled(the_part, intent, llm, events, attachments):
    """
    One turn over what the page-sized calls produced, seeing no pictures —
    every named list `shown()` would draw one beside, as `_words_only`
    gives it instead. The half of cross-view reading a page cannot do
    itself: it sees every page's words at once and settles what only the
    whole sheet can.
    """
    return design({"intent": intent, "the_part_as_you_described_it": the_part},
                  llm, events, attachments, pictures=False)
