"""Prompt composition, streaming, and parsing utilities."""
import base64
import json
import mimetypes
import os
import re
import time

from core.events import CALL_STARTED, COST, THOUGHT, WRITING
from core.freecad_apply import TRAPS
from core.llm import REASONING, StreamCutOff
from core.sheets import sheet_reader

from core.loop.budgets import PhaseError


DESIGN_PHASE = "design"


PLAN_PHASE = "plan"


EXECUTE_PHASE = "execute"


ASSERTION_PHASE = "assertion"


REVIEW_SUSPECT_PHASE = "review_suspect"


REVIEW_PHASE = "review"


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


from core.prompts import read_prompt


_LANGUAGE_HEADINGS = (
    "Naming from a closed vocabulary",
    "The operation is one of 36",
    "A Step is one sentence",
    "The artefacts are English",
)


_PHASE_VOCABULARY = {
    DESIGN_PHASE: ("Naming from a closed vocabulary", "The artefacts are English"),
    PLAN_PHASE: ("The operation is one of 36", "A Step is one sentence",
                 "The artefacts are English"),
    EXECUTE_PHASE: ("The operation is one of 36", "The artefacts are English"),
    ASSERTION_PHASE: ("The artefacts are English",),
    REVIEW_SUSPECT_PHASE: ("The artefacts are English",),
    REVIEW_PHASE: ("The artefacts are English",),
    "contract_correction": (),
    "budget_last_call": (),
}


_TRAPS_PLACEHOLDER = "{{traps}}"


_CUT_OFF_ATTEMPTS = 3


_LEDGER_HEADING = "This run has already failed these ways:"


def _language_sections():
    """
    `config/prompts/language.md` split at its own `## ` headings, each
    section keyed by heading text — the unit `_PHASE_VOCABULARY` composes
    from, so a phase gets the sections it uses and nothing it does not.
    """
    text = read_prompt("language.md")
    marks = sorted((text.index("## " + heading), heading)
                   for heading in _LANGUAGE_HEADINGS)
    sections = {}
    for i, (start, heading) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        sections[heading] = text[start:end].strip()
    return sections


def prompt(phase, ledger=""):
    """
    A phase's system prompt, read from `config/prompts/<phase>.md` and
    composed with the sections of the shared vocabulary (ADR-0026).
    """
    body = read_prompt(phase + ".md").strip()
    sections = _language_sections()
    vocabulary = "\n\n".join(sections[heading] for heading in _PHASE_VOCABULARY[phase])
    body = "%s\n\n%s" % (body, vocabulary)
    body = body.replace(_TRAPS_PLACEHOLDER, "\n".join("- " + trap for trap in TRAPS))
    if ledger:
        body = "%s\n\n%s\n%s" % (body, _LEDGER_HEADING, ledger)
    return body


def _ask(llm, events, phase, system, user):
    """
    One phase's turn: stream it, surface the reasoning as it forms, parse the reply.

    A reply carrying no object is asked once more as a second turn: kimi-k3
    replied to Execute with prose that was a *look*, which is a key of the
    object — the model had the answer and wrote it as a sentence. One re-ask
    costs a turn; the alternative cost the run, ten approved Steps into it.

    Of the last 20 sessions, 11 touched Design and 8 of those died here,
    70-640 s in — one more turn is worth trying before the phase fails.
    """
    body, thought, reasoning = _streamed(llm, events, phase, system, user)
    if not isinstance(_object_of(body), dict):
        body, thought, reasoning = _streamed(llm, events, phase, system, user,
                                             prior=_asked_again(body, reasoning))

    if thought and not body.strip():
        raise PhaseError(
            "%s streamed its reasoning and then stopped without answering. Send the "
            "Intent again, or pick a model that thinks less before it commits." % phase
        )
    return _as_json(body, phase)


def _streamed(llm, events, phase, system, user, prior=()):
    """
    One turn, as the text it wrote, whether it reasoned, and the reasoning.

    A stream the provider closed mid-reply is sent again. Of seven long
    streams measured against the opencode gateway four were cut and three
    finished, so the second attempt usually lands. Three attempts, because a
    gateway that cuts all three is down rather than unlucky.
    """
    for attempt in range(_CUT_OFF_ATTEMPTS):
        try:
            return _one_call(llm, events, phase, system, user, prior)
        except StreamCutOff:
            if attempt == _CUT_OFF_ATTEMPTS - 1:
                raise
            time.sleep(2 ** attempt)


def _one_call(llm, events, phase, system, user, prior=()):
    """
    The turn itself, priced whether it answered or the connection went.

    Every phase's call passes through here, which is what makes this the seam
    a per-phase client is chosen at: `for_phase` is asked for the client this
    phase is answered by, and a client that has none answers all five itself.
    Design reading a drawing and Execute writing the code for a Step it has
    already been given are not the same demand, and one dial over both is
    paid at the rate of the more expensive on every call of the cheaper.

    The reply reaches the stream as it forms, the way the reasoning before it
    does. Until it did, a phase went silent from its last `thought` to its
    `cost` — 22.4 minutes of a recorded run's 23.6 minutes of provider time,
    and 25.6 of 53.7 in another — and a panel with nothing to show reads as a
    run that has hung.

    `cost` carries whatever the client reported spending: `usd` when a price
    is known, and `prompt_tokens`/`completion_tokens`/`cached_tokens` when the
    provider counted them, both routes do. A client that answers
    with neither — `ScriptedLLMClient` — still gets a `cost` with `seconds`
    alone, because the wait is a real one.
    """
    llm = llm.for_phase(phase) if hasattr(llm, "for_phase") else llm
    ceiling = getattr(llm, "timeout", None)
    model = getattr(llm, "model", None)
    started = {"phase": phase, "model": model}
    if ceiling is not None:
        started["ceiling"] = ceiling
    events.emit(CALL_STARTED, **started)

    start = time.time()
    text, reasoning, thought = [], [], False
    messages = [{"role": "system", "content": system},
               {"role": "user", "content": user}] + list(prior)
    try:
        for chunk in llm.stream(messages):
            if chunk.kind == REASONING:
                thought = True
                reasoning.append(chunk.text)
                events.emit(THOUGHT, phase=phase, text=chunk.text)
            else:
                text.append(chunk.text)
                events.emit(WRITING, phase=phase, text=chunk.text)
    finally:
        spent = getattr(llm, "last_cost", None) or {}
        paid = {"phase": phase, "seconds": time.time() - start,
                "model": spent.get("model", model)}
        for key in ("usd", "prompt_tokens", "completion_tokens", "cached_tokens"):
            if key in spent:
                paid[key] = spent[key]
        events.emit(COST, **paid)
    return "".join(text), thought, "".join(reasoning)


_ASKED_AGAIN = read_prompt("asked_again.md")


def _asked_again(body, reasoning):
    """
    The retry as a second turn of the same conversation: the model's own last
    reply, handed back as its own assistant message, then a short nudge asking
    for the object — not the original `user` sent again with a directive
    stapled on. The evidence a phase like Design carries is in that original
    `user`, once, and a retry that resent it was a second prefill of the same
    tens of thousands of tokens for the sake of one sentence at the end.

    `reasoning` stands in for `body` when there is no reply to hand back — a
    turn spent entirely on thought and nothing after it. There is no promise
    the tail of a chain of thought reads as an answer; there is a promise it
    reads as *something*, which a blank assistant turn does not, and which is
    what a retry needs to be a turn of this conversation rather than a demand
    into empty air.
    """
    said = (body or reasoning or "").strip() or "(nothing was said)"
    return [{"role": "assistant", "content": said},
            {"role": "user", "content": _ASKED_AGAIN}]


def _as_image(path):
    """
    Read a file as the content block the provider takes for it.

    A path is meaningless to the model — it cannot open files. Returns None if
    there is nothing to show, which a sketch-only Step produces.

    The mime comes off the file rather than being stamped `image/png` on
    everything, which is how a JPEG went out labelled as a PNG. A PDF
    is a `file` block rather than an `image_url` one — the same path with a
    different envelope, and no page rendering on our side. The renders we write
    ourselves are PNGs, so that path is unchanged.
    """
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")

    mime, _ = mimetypes.guess_type(path)
    mime = mime or "application/octet-stream"
    if mime == "application/pdf":
        return {"type": "file",
                "file": {"file_data": "data:%s;base64,%s" % (mime, encoded)}}
    return {"type": "image_url",
            "image_url": {"url": "data:%s;base64,%s" % (mime, encoded)}}


def _as_json(text, phase):
    """
    The reply as the object it carries, wherever in the reply that object is.

    A phase that answered correctly and then explained itself used to end the
    run. Measured on `logs/sessions/6fbd31e98fb540818086611841fa9aff.jsonl`:
    Design replied `{"design": {}, "question": ""}` and then a paragraph saying
    why the two strings it was asked about are the item numbers of a notes list
    and not dimensions — a right answer, a useful explanation, and a dead run,
    because the whole reply had to parse or none of it did.

    So the fence is tried, then the whole reply, then the first balanced object
    in it. Only an object: a reply with no JSON in it at all is still a phase
    that did not do its job, and it still says so quoting what was said.
    """
    parsed = _object_of(text)
    if parsed is None:
        raise PhaseError(
            "%s did not reply with JSON. It said: %s" % (phase, (text or "").strip()[:400])
        )
    if not isinstance(parsed, dict):
        raise PhaseError("%s replied with JSON that is not an object: %r" % (phase, parsed))
    return parsed


def _object_of(text):
    """The object a reply carries, by any of the three routes, or None."""
    fenced = _FENCE.search(text or "")
    candidate = (fenced.group(1) if fenced else (text or "")).strip()
    try:
        return json.loads(candidate)
    except ValueError:
        return _the_object_in(candidate)


def _the_object_in(text):
    """
    The first balanced `{...}` in a reply, parsed, or None.

    Balanced rather than greedy: the last `}` in the reply belongs to whatever
    the model wrote last, which on a reply that ends in prose is a sentence with
    a brace quoted in it. Strings are stepped over so a brace inside one does
    not close the object.
    """
    start = text.find("{")
    while start != -1:
        depth, at, in_string, escaped = 0, start, False, False
        while at < len(text):
            character = text[at]
            if in_string:
                in_string = not (character == '"' and not escaped)
                escaped = character == "\\" and not escaped
            elif character == '"':
                in_string, escaped = True, False
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:at + 1])
                    except ValueError:
                        break
            at += 1
        start = text.find("{", start + 1)
    return None


def _pictures(attachments):
    return [picture for picture in map(_as_image, attachments) if picture]


def _sheets(attachments):
    """Every attachment this run could read as a sheet, in the order shown."""
    return [sheet for sheet in (sheet_reader.read(path) for path in attachments)
            if sheet is not None]


def _the_evidence(attachments, views=None, keep_printed=False, pictures=True):
    """
    What Design is shown: the sheet cut up and its text layer extracted.

    A drawing used to go out whole at 143 DPI for an 11 x 8.5 in sheet, which
    puts 14 px of ink in a dimension digit and misreads it occasionally
    rather than reliably. It now goes out as one picture per view at the DPI
    its own callouts ask for.
    """
    shown = []
    for path in attachments:
        sheet = sheet_reader.read(path)
        if sheet is None:
            if pictures:
                picture = _as_image(path)
                if picture:
                    shown.append(picture)
            continue
        if not pictures:
            shown.extend({"type": "text", "text": text} for text in _words_only(sheet))
            continue
        for piece in _pieces_of(sheet, views):
            if "say" in piece:
                shown.append({"type": "text", "text": piece["say"]})
            else:
                picture = _as_image(piece["show"])
                if picture:
                    shown.append(picture)
        if keep_printed and views is not None and sheet.printed:
            shown.append({"type": "text", "text": sheet_reader._the_printed_list(sheet)})
    return shown


def _pieces_of(sheet, views):
    """*sheet*'s own `shown()`, narrowed to `views` — the one place both
    `_the_evidence` and the page-budget estimate in `_group_tokens` read it,
    so the two never drift apart about what a turn over `views` actually
    sends."""
    return _narrowed(sheet_reader.shown(sheet), _wanted_paths(sheet, views))


def _words_only(sheet):
    """
    Every named list `shown()` draws a picture beside, as words alone.

    `_reconciled` is the turn that settles what only the whole sheet can
    settle, and it does that off what is printed, leadered, measured between
    two points and drawn — not off a picture, because the page-sized turns
    that came before it have already looked at every picture there is.
    """
    parts = []
    if sheet.printed:
        parts.append(sheet_reader._the_printed_list(sheet))
    if sheet.leaders:
        parts.append(sheet_reader._the_leader_list(sheet))
    if sheet.between:
        parts.append(sheet_reader._the_between_list(sheet))
    if sheet.drawn:
        parts.append(sheet_reader._the_drawn_list(sheet))
    return parts


def _wanted_paths(sheet, views):
    """
    The picture paths a narrowed turn keeps: the sheet overview, always, and
    the whole/crop pictures of the named `views` — or `None`, meaning every
    picture, when `views` itself is `None`.

    Read off `sheet`'s own fields rather than off `shown()`'s prose, so this
    does not depend on how a view is announced, only on which picture belongs
    to it — `sheet_reader.shown(sheet)`'s signature is the only promise this
    makes use of.
    """
    if views is None:
        return None
    wanted = set(views)
    paths = {sheet.overview}
    paths.update(sheet.overviews or ())
    paths.update(whole.path for whole in sheet.wholes if whole.name in wanted)
    paths.update(crop.path for crop in sheet.crops
                 if crop.name.split(", part ")[0] in wanted)
    return paths


def _narrowed(told, paths):
    """
    *told* kept down to `paths` — or all of it, when `paths` is `None`.

    A `say` that introduces a picture is kept exactly when that picture is; a
    `say` with no picture after it is one of the sheet-wide lists (the printed
    callouts, the leaders, the notes) and is dropped on a narrowed turn along
    with the pictures — what a narrowed turn asks about has already named the
    callout it wants, in the question, so the sheet's own account of every
    callout is not needed again to answer it.
    """
    if paths is None:
        return told
    kept = []
    for index, piece in enumerate(told):
        if "show" in piece:
            if piece["show"] in paths:
                kept.append(piece)
            continue
        following = told[index + 1] if index + 1 < len(told) else None
        if following is not None and "show" in following and following["show"] in paths:
            kept.append(piece)
    return kept


_CHARS_PER_TOKEN = 4
