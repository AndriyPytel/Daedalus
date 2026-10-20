"""Prompt composition and parsing utilities."""
import base64
import json
import mimetypes
import os
import re

from core.freecad_apply import TRAPS
from core.lint import OP_TYPEIDS
from core.nobody import nobody_is_answering
from core.sheets import sheet_reader


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
    "declaration_limit": (),
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


_ASKING = re.compile(r"\{\{asking\}\}(.*?)\{\{/asking\}\}", re.DOTALL)


_NOBODY = re.compile(r"\{\{nobody\}\}(.*?)\{\{/nobody\}\}", re.DOTALL)


_EDITING = re.compile(r"\{\{editing\}\}(.*?)\{\{/editing\}\}", re.DOTALL)


_VECTOR = re.compile(r"\{\{vector\}\}(.*?)\{\{/vector\}\}", re.DOTALL)


_RASTER = re.compile(r"\{\{raster\}\}(.*?)\{\{/raster\}\}", re.DOTALL)


def a_raster_picture(attachments):
    """Whether the drawing is a picture, and so has no text layer or line work to read."""
    return bool(attachments) and not any(str(path).lower().endswith(".pdf") for path in attachments)


def _typeid_table():
    """The verb-to-TypeId table the op_mismatch gate checks against, one verb a line."""
    return "\n".join("- %s: %s" % (op, ", ".join(typeids) or "(builds no feature of its own)")
                     for op, typeids in OP_TYPEIDS.items())


def prompt(phase, ledger="", editing=False, raster=False):
    """
    A phase's system prompt, read from `config/prompts/<phase>.md` and
    composed with the sections of the shared vocabulary. `{{editing}}`
    passages stay only where the run edits a base part; `{{raster}}` passages
    stay only for a picture, `{{vector}}` ones only where it is not.
    """
    body = read_prompt(phase + ".md").strip()
    sections = _language_sections()
    vocabulary = "\n\n".join(sections[heading] for heading in _PHASE_VOCABULARY[phase])
    body = "%s\n\n%s" % (body, vocabulary)
    asking, nobody = (r"", r"\1") if nobody_is_answering() else (r"\1", r"")
    body = _NOBODY.sub(nobody, _ASKING.sub(asking, body))
    body = _EDITING.sub(r"\1" if editing else r"", body)
    body = _VECTOR.sub(r"" if raster else r"\1", _RASTER.sub(r"\1" if raster else r"", body))
    body = body.replace(_TRAPS_PLACEHOLDER, "\n".join("- " + trap for trap in TRAPS))
    body = body.replace("{{typeids}}", _typeid_table())
    if ledger:
        body = "%s\n\n%s\n%s" % (body, _LEDGER_HEADING, ledger)
    return body


def _as_image(path):
    """
    Read a file as the content block the provider takes for it. None
    when there is nothing to show, which a sketch-only Step produces.
    The mime comes off the file rather than a stamped `image/png`, that
    once mislabelled a JPEG, and a PDF is a `file` block, not
    `image_url`, unrendered.
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

    Balanced rather than greedy: the last `}` belongs to whatever the model
    wrote last, which on a reply ending in prose is a brace quoted in a
    sentence. Strings are stepped over so a brace inside one does not close it.
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

    Sent as one picture per view at the DPI its own callouts ask for — a
    whole 11 x 8.5 in sheet at 143 DPI puts 14 px of ink in a dimension digit
    and misreads it occasionally rather than reliably.
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

    `_reconciled` settles what only the whole sheet can, off what is
    printed, leadered, measured between two points and drawn — not off a
    picture, since the page-sized turns before it already looked at every one.
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
    the whole/crop pictures of `views` — or `None`, every picture, when
    `views` is `None`. Read off `sheet`'s own fields, not `shown()`'s prose,
    so this depends only on `sheet_reader.shown(sheet)`'s signature.
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

    A `say` introducing a picture is kept exactly when that picture is; a
    `say` with none after it is a sheet-wide list and drops with the
    pictures — a narrowed turn's question already names the callout it wants.
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
