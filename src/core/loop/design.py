"""The Design phase: describe the part from attachments."""
import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor

from core.design.design import A_CHECK_HAZARD, merge
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


def design(body, llm, events, attachments=(), views=None, phase=DESIGN_PHASE,
          keep_printed=False, pictures=True):
    """
    One Design turn: what it adds to the part, and what it needs a person for.

    Every turn of the phase comes through here — the first pass at the sheet, a
    question put back to it, a person's answer being written in — because every
    one of them ends in the same place, a merge into the same model. Merge is
    the only way the description grows, so there is never an answer standing
    beside it to be adjudicated (ADR-0019). What differs between the turns is
    the body, and that is all that differs.

    A context of its own, with nothing to build (ADR-0018, kept): Design and
    Plan used to be one call, and the second pressure spoilt the first — a value
    was invented not because the model saw badly but because it needed something
    to plan with. The sheet rides along whenever there is one, because the phase
    that owns the description is the only phase that ever sees the picture.

    The Intent is in the body of every turn, beside whatever else that turn is
    asking about. It is the scope of the run rather than one source among them,
    so a turn without it reads the sheet for nothing in particular; it never
    narrows what is described, because what the drawing prints is on the part
    whether the run was asked about it or not.

    `views`, `keep_printed` and `pictures` are `_the_evidence`'s own, passed
    through rather than applied here: this is where the evidence and the body
    meet, not where either is decided.

    `phase` labels the call on the event stream and the session log — plain
    `"design"` for every turn except the page-sized ones `_first_pass` fans
    out, which are told apart as `"design: <label>"` so N calls in flight are
    N legible rows and not one stream with N reasonings braided into it.

    The evidence goes out first and the body — the one part of the request
    that changes turn to turn — last, so the evidence is a stable prefix
    rather than the tail of a message that starts differently every time. On
    the one route measured to honour it (`core/llm.py :: cache_control_supported`)
    its last block carries `cache_control`, so a run's second and later Design
    turns read the prefix from cache instead of paying to send it again; on
    every other route the block carries an extra key the provider was never
    asked about and nothing here relies on it doing anything with it.

    Hands back what to merge and what Design wants asked. A reply that is not a
    model grows the part by nothing, which is the same case as a sheet that does
    not answer: the run carries on with what it already had rather than losing
    it, the way an acceptance list nobody wrote is not fatal either.
    """
    evidence = _the_evidence(attachments, views, keep_printed, pictures)
    if evidence and cache_control_supported(getattr(llm, "model", None)):
        evidence = evidence[:-1] + [dict(evidence[-1], cache_control={"type": "ephemeral"})]
    reply = _ask(llm, events, phase, prompt(DESIGN_PHASE),
                 evidence + [{"type": "text", "text": json.dumps(body, indent=2, default=str)}])
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
    run in parallel, each merged into the same part as it lands
    (ADR-0019 — grow-only and order-independent is what makes concurrent
    merges legitimate rather than a race). Falls back to today's single
    whole-attachment call when `_the_groups` finds nothing to page.

    `copy.copy(llm)` gives every concurrent call its own `last_cost`: both
    `LiteLLMClient` and `ClaudeCodeClient` clear that attribute at the top of
    `stream()` and set it at the end, as instance state, and two calls
    sharing one `llm` object under real concurrency would clear and set each
    other's price mid-flight. The copy is shallow and cheap — model, key and
    the spawn/completion callable are read-only, so nothing about the
    provider route changes, only which object holds this one call's price.

    `cancelled()` is read once, before the batch is submitted, exactly where
    every other call site in this phase reads it: a provider call has no
    cancel hook to give a thread mid-flight, so a run stopped while the batch
    is in flight still lets every submitted call finish, the same as a run
    stopped mid-call does today.

    A group's own failure — no JSON, a refusal, a stream that would not stay
    up — costs that group and not the phase: it lands on the part as a
    `conflicts` entry naming the group, with `kind=A_CHECK_HAZARD`, the same
    shape `_the_geometry_disagrees` gives an outline the solver never reached,
    and it is said once more on the `ERROR` stream so a session log shows
    which page went unread and why.

    At most one question crosses the fan-out, the first a group asked in page
    order: the phase still asks once here (ADR-0022), and a page-sized call
    asking on its own for its own reasons is not license for the run to wait
    on several at once.
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
