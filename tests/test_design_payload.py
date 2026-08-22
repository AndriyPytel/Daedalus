"""
Level 1 — what one Design turn costs, as a number a test produces.

`core.loop._the_evidence` rebuilds the whole sheet payload on every Design
turn: `core.sheet_reader.read` caches the render, so the pictures are not
re-rendered, but every picture and every extracted text block is re-sent.
This is the number that work against that call has to move, measured off two
distinct Model Mania sheets rather than asserted in a docstring or a commit
message.

Visual tokens are `width * height / 750`, the estimator a provider charges an
image at — verified here against the design-phase measurement already on
record: the 2023 sheet's 27 pictures work out to ~21,400 visual tokens and its
43,918 characters of text blocks to ~41,300 total input tokens once
`config/prompts/design.md` (~9k tokens) is added, both matching this module's
own count to within the recorded rounding. Text tokens are characters / 4, the
same ratio that turns `design.md`'s 35,684 characters into ~9k tokens.

A tiled view no longer sends its own whole picture (`core/sheet_reader.py`),
which is most of why the picture count and the visual tokens fell together
with `fcc-sheet`'s change; a placed picture no region reaches now sends a
low-DPI `shape_only` crop of its own, which is why the characters rose a
little even as the pictures fell — a caption for a crop that did not exist
before.

The breakdown by list is by equality against the four functions
`core.sheet_reader.shown` calls to say what is printed, leadered, measured
between two points, and drawn: a "say" piece the report cannot attribute to
one of those is either a caption `shown` writes inline (a page label, a view's
whole-picture introduction) or one of the sheet's own `notes`.
"""
import json
import os

import pytest

from core import loop, sheet_reader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELMANIA = os.path.join(ROOT, "tests", "modelmania")
DESIGN_PROMPT = os.path.join(ROOT, "config", "prompts", "design.md")
REPORT_PATH = "/tmp/FreeCadCopilot/design_payload_report.json"

ATTACHMENTS = {
    "model-mania_-in-person-2023.pdf": os.path.join(
        MODELMANIA, "model-mania_-in-person-2023.pdf"),
    "model-mania-2024.pdf": os.path.join(MODELMANIA, "model-mania-2024.pdf"),
}

CHARS_PER_TOKEN = 4
PIXELS_PER_VISUAL_TOKEN = 750

BASELINE = {
    "model-mania_-in-person-2023.pdf": {
        "pictures": 27, "visual_tokens": 21401, "total_chars": 43918,
        "input_tokens": 41301,
    },
    "model-mania-2024.pdf": {
        "pictures": 28, "visual_tokens": 19471, "total_chars": 44906,
        "input_tokens": 39618,
    },
}

needs_pymupdf_and_pillow = pytest.mark.skipif(
    not sheet_reader.available(), reason="PyMuPDF is not installed")


def _design_prompt_tokens():
    with open(DESIGN_PROMPT, encoding="utf-8") as handle:
        return len(handle.read()) // CHARS_PER_TOKEN


def _payload_report(path):
    """
    One Design turn's payload for one attachment, read off the real code path.

    Rebuilds the sheet and re-derives its `shown()` pieces on every call, the
    way `core.loop._the_evidence` does on every turn — nothing here is cached
    across calls, so calling this twice for the same path is what "a second
    Design turn" costs today.
    """
    from PIL import Image

    sheet = sheet_reader.read(path)
    if sheet is None:
        return None

    named_lists = {
        "printed_on_the_sheet": (
            sheet_reader._the_printed_list(sheet) if sheet.printed else None),
        "leaders": sheet_reader._the_leader_list(sheet) if sheet.leaders else None,
        "between": sheet_reader._the_between_list(sheet) if sheet.between else None,
        "drawn": sheet_reader._the_drawn_list(sheet) if sheet.drawn else None,
    }

    pictures = 0
    visual_tokens = 0.0
    chars_by_bucket = {name: 0 for name in named_lists}
    chars_by_bucket.update(notes=0, captions=0)

    for piece in sheet_reader.shown(sheet):
        if "say" in piece:
            text = piece["say"]
            bucket = next((name for name, said in named_lists.items()
                           if said == text), None)
            if bucket is None:
                bucket = "notes" if text in sheet.notes else "captions"
            chars_by_bucket[bucket] += len(text)
        else:
            pictures += 1
            with Image.open(piece["show"]) as picture:
                width, height = picture.size
            visual_tokens += width * height / PIXELS_PER_VISUAL_TOKEN

    total_chars = sum(chars_by_bucket.values())
    evidence = loop._the_evidence([path])
    evidence_pictures = sum(
        1 for block in evidence if block.get("type") in ("image_url", "file"))
    evidence_chars = sum(
        len(block["text"]) for block in evidence if block.get("type") == "text")
    assert (pictures, total_chars) == (evidence_pictures, evidence_chars), (
        "shown() and _the_evidence() disagree about what one Design turn sends")

    input_tokens = (_design_prompt_tokens() + total_chars // CHARS_PER_TOKEN
                    + round(visual_tokens))

    return {
        "pictures": pictures,
        "visual_tokens": round(visual_tokens),
        "chars_by_bucket": chars_by_bucket,
        "total_chars": total_chars,
        "input_tokens": input_tokens,
    }


@needs_pymupdf_and_pillow
def test_the_design_payload_does_not_grow_past_its_measured_baseline():
    """
    Every figure in `BASELINE` is what today's code sends; the assertion is
    `<=` against it rather than `==`, so a change that shrinks the payload
    passes and one that grows it fails. A second Design turn is measured the
    same way and costs the same as the first — nothing in the payload is
    deduplicated across turns today, and that equality is itself a fact this
    test pins down.
    """
    report = {}
    for name, path in ATTACHMENTS.items():
        first_turn = _payload_report(path)
        second_turn = _payload_report(path)
        report[name] = {"first_turn": first_turn, "second_turn": second_turn}

        assert second_turn == first_turn, (
            "%s: a second Design turn no longer costs what the first one did"
            % name)

        baseline = BASELINE[name]
        for key in ("pictures", "visual_tokens", "total_chars", "input_tokens"):
            assert first_turn[key] <= baseline[key], (
                "%s: %s grew from %d to %d"
                % (name, key, baseline[key], first_turn[key]))

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)

    print("\ndesign payload report -> %s" % REPORT_PATH)
    for name, turns in report.items():
        first = turns["first_turn"]
        print("\n%s" % name)
        print("  pictures=%d visual_tokens=%d total_chars=%d input_tokens=%d"
              % (first["pictures"], first["visual_tokens"], first["total_chars"],
                 first["input_tokens"]))
        for bucket, chars in first["chars_by_bucket"].items():
            print("    %-20s %6d chars" % (bucket, chars))
