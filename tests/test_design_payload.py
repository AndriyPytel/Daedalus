"""
Level 1 — what a Design first pass costs, as a number a test produces.

`core._first_pass` no longer sends one whole-sheet call: it cuts a sheet
into page-sized groups (`core._the_groups`), asks each in parallel, and
finishes with one reconciling call that sees no pictures at all
(`core._reconciled`). This is the number that work against the fan-out
has to move, measured off two distinct Model Mania sheets rather than
asserted in a docstring or a commit message.

Visual tokens are `width * height / 750`, the estimator a provider charges an
image at. Text tokens are characters / 4, the same ratio that turns
`design.md`'s characters into tokens. `SINGLE_CALL_BASELINE` is what one
whole-sheet call cost before the fan-out — kept here only as the denominator
`FIRST_PASS_CEILING_RATIO` is a multiple of, not as its own assertion: a real
multi-view sheet no longer takes that call at all.

`FIRST_PASS_CEILING_RATIO` is measured, not chosen: it is the smallest ratio
that passes the current baselines. Raising it requires re-measurement and a
stated reason. The current value (1.69) is what Design's own call is
charged for the shared vocabulary it actually uses (ADR-0026, FCC-234):
`core.prompt` composes each phase from the sections of `config/prompts/
language.md` that phase reads, not the whole file, so Design carries the
closed-naming section and the English-only rule — it neither writes nor
checks an `op`, so the 36-verb section and the Step-sentence-rules section
stay out of its own call. That cost buys name convergence across pages and
a `kind` two readings can actually compare, so merge() sees a real
disagreement instead of two synonyms passing each other.

The ratio was 1.65 until a measured callout began carrying the two feet of
its own dimension line on its own line: 953 characters on the 2023 sheet and
1,064 on the 2024 one, per page-sized call, or 1.9% of the first pass. What it
buys is the attribution the feet decide — a run that read the 7 and the 14 of
the 2023 bottom view off the tile they are legible in gave both to a feature
it invented, and rebuilt the part around it.

The ratio moved again to 1.71 when `design.md` gained FCC-322's own "How you
work" section, stating the multi-turn `{"code"}`/`{"delegate"}`/`{"declare"}`
contract FCC-321's agent loop actually runs on — every page-sized call is
Design's own composed prompt plus that page's evidence, so a section every
call carries prices in on every one of them, not once.
"""
import json
import os

import pytest
from core.loop.design import _first_pass, _group_tokens, _reconciled, _the_groups
from core.loop.prompt import _the_evidence, prompt, DESIGN_PHASE

from core.sheets import sheet_reader
from tests import modelmania_harness

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELMANIA = str(modelmania_harness.MODELMANIA_DIR)
REPORT_PATH = "/tmp/FreeCadCopilot/design_payload_report.json"

ATTACHMENTS = {
    "model-mania_-in-person-2023.pdf": os.path.join(
        MODELMANIA, "model-mania_-in-person-2023.pdf"),
    "model-mania-2024.pdf": os.path.join(MODELMANIA, "model-mania-2024.pdf"),
}

CHARS_PER_TOKEN = 4
PIXELS_PER_VISUAL_TOKEN = 750

SINGLE_CALL_BASELINE = {
    "model-mania_-in-person-2023.pdf": 41653,
    "model-mania-2024.pdf": 39970,
}

FIRST_PASS_CEILING_RATIO = 1.71

BASELINE = {
    "model-mania_-in-person-2023.pdf": {
        "groups": [
            {"label": "page 1", "pictures": 15, "total_chars": 7195, "input_tokens": 23919},
            {"label": "page 2", "pictures": 14, "total_chars": 6999, "input_tokens": 24657},
        ],
        "reconcile": {"total_chars": 40545, "input_tokens": 20386},
        "total": 68962,
    },
    "model-mania-2024.pdf": {
        "groups": [
            {"label": "page 1", "pictures": 15, "total_chars": 8636, "input_tokens": 22254},
            {"label": "page 2", "pictures": 15, "total_chars": 8539, "input_tokens": 25138},
        ],
        "reconcile": {"total_chars": 41375, "input_tokens": 20593},
        "total": 67985,
    },
}

needs_pymupdf_and_pillow = pytest.mark.skipif(
    not sheet_reader.available(), reason="PyMuPDF is not installed")


def _design_prompt_tokens():
    """
    What Design's own system prompt actually costs per call: `core.loop.
    prompt`'s composed text (`design.md` plus the shared vocabulary,
    `language.md`, since FCC-234), not `design.md` read on its own — a first
    pass that leaves `language.md` out of its own cost is undercounted
    against the calls it is really measuring.
    """
    return len(prompt(DESIGN_PHASE)) // CHARS_PER_TOKEN


def _tokens_of(evidence, prompt_tokens):
    """One call's evidence, priced the same way `core._group_tokens` prices
    a candidate group before it is sent — the picture at its pixel count over
    750, the text at four characters to the token."""
    from PIL import Image

    chars = sum(len(block["text"]) for block in evidence if block.get("type") == "text")
    visual = 0.0
    for block in evidence:
        if block.get("type") != "image_url":
            continue
        header, encoded = block["image_url"]["url"].split(",", 1)
        import base64
        import io
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as picture:
            visual += picture.width * picture.height / PIXELS_PER_VISUAL_TOKEN
    pictures = sum(1 for block in evidence if block.get("type") == "image_url")
    return {"pictures": pictures, "total_chars": chars,
            "input_tokens": prompt_tokens + chars // CHARS_PER_TOKEN + round(visual)}


def _first_pass_report(path):
    """
    One first pass's payload for one attachment, read off the real code
    path: `core._the_groups` for the grouping, `core._the_evidence`
    for what each group and the reconciling call actually send. Nothing here
    is cached across calls, so calling this twice for the same path is what
    a second first pass costs today.
    """
    sheet = sheet_reader.read(path)
    if sheet is None:
        return None

    prompt_tokens = _design_prompt_tokens()
    groups = _the_groups([path])
    reported_groups = []
    for label, views in groups:
        evidence = _the_evidence([path], views=views, keep_printed=True)
        reported_groups.append(dict(label=label, **_tokens_of(evidence, prompt_tokens)))

    reconcile_evidence = _the_evidence([path], pictures=False)
    reconcile = _tokens_of(reconcile_evidence, prompt_tokens)
    assert reconcile["pictures"] == 0, "the reconciling call was shown a picture"
    del reconcile["pictures"]

    total = sum(group["input_tokens"] for group in reported_groups) + reconcile["input_tokens"]
    return {"groups": reported_groups, "reconcile": reconcile, "total": total}


@needs_pymupdf_and_pillow
def test_the_first_pass_does_not_grow_past_its_measured_baseline():
    """
    Every figure in `BASELINE` is what today's code sends; the assertion is
    `<=` against it rather than `==`, so a change that shrinks the payload
    passes and one that grows it fails. A second first pass is measured the
    same way and costs the same as the first — nothing in the payload is
    deduplicated across first passes today, and that equality is itself a
    fact this test pins down.
    """
    report = {}
    for name, path in ATTACHMENTS.items():
        first_pass = _first_pass_report(path)
        second_pass = _first_pass_report(path)
        report[name] = {"first_pass": first_pass, "second_pass": second_pass}

        assert second_pass == first_pass, (
            "%s: a second first pass no longer costs what the first one did" % name)

        baseline = BASELINE[name]
        assert len(first_pass["groups"]) == len(baseline["groups"]), (
            "%s: the sheet no longer pages the way the baseline was measured against"
            % name)
        for grown, held in zip(first_pass["groups"], baseline["groups"]):
            assert grown["label"] == held["label"], "%s: the pages changed order" % name
            for key in ("pictures", "total_chars", "input_tokens"):
                assert grown[key] <= held[key], (
                    "%s: %s of %s grew from %d to %d"
                    % (name, key, grown["label"], held[key], grown[key]))
        for key in ("total_chars", "input_tokens"):
            assert first_pass["reconcile"][key] <= baseline["reconcile"][key], (
                "%s: the reconciling call's %s grew from %d to %d"
                % (name, key, baseline["reconcile"][key], first_pass["reconcile"][key]))

        assert first_pass["total"] <= baseline["total"], (
            "%s: the first pass total grew from %d to %d"
            % (name, baseline["total"], first_pass["total"]))
        ceiling = FIRST_PASS_CEILING_RATIO * SINGLE_CALL_BASELINE[name]
        assert first_pass["total"] <= ceiling, (
            "%s: the first pass costs %d, past %.1fx the single call it replaced (%d)"
            % (name, first_pass["total"], FIRST_PASS_CEILING_RATIO, ceiling))

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)

    print("\ndesign payload report -> %s" % REPORT_PATH)
    for name, turns in report.items():
        first = turns["first_pass"]
        print("\n%s (total %d input tokens)" % (name, first["total"]))
        for group in first["groups"]:
            print("  %-10s pictures=%d total_chars=%d input_tokens=%d"
                  % (group["label"], group["pictures"], group["total_chars"],
                     group["input_tokens"]))
        print("  %-10s total_chars=%d input_tokens=%d"
              % ("reconcile", first["reconcile"]["total_chars"],
                 first["reconcile"]["input_tokens"]))
