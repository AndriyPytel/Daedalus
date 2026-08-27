"""
`config/prompts/language.md` is the single home of the vocabulary every
phase writes and reads (ADR-0026, FCC-234): the closed `kind` and `op`
lists, the noun-to-verb map, and the rule that an artefact is written in
English, one heading per closed list. `core.loop.prompt` composes each
phase from `_PHASE_VOCABULARY`'s sections for it, not the whole file — a
phase's own prompt file never repeats a term any section defines.
"""
import os

from core.loop import (ASSERTION_PHASE, DESIGN_PHASE, EXECUTE_PHASE,
                        PLAN_PHASE, REVIEW_PHASE, _LANGUAGE_HEADINGS,
                        _PHASE_VOCABULARY, _language_sections, prompt)

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "config", "prompts")
LANGUAGE_MD = os.path.join(PROMPTS_DIR, "language.md")

PHASES = (DESIGN_PHASE, PLAN_PHASE, EXECUTE_PHASE, ASSERTION_PHASE, REVIEW_PHASE)


def _language_md():
    with open(LANGUAGE_MD, encoding="utf-8") as f:
        return f.read().strip()


def test_every_phase_prompt_is_composed_from_the_sections_it_uses():
    sections = _language_sections()

    for phase in PHASES:
        composed = prompt(phase)
        for heading in _PHASE_VOCABULARY[phase]:
            assert sections[heading] in composed, (phase, heading)


def test_a_phase_never_carries_a_section_it_was_not_given():
    """
    The point of composing sections rather than the whole file: Design never
    writes an `op`, so its own call is never charged for `"The operation is
    one of 36"` — the section `_PHASE_VOCABULARY[DESIGN_PHASE]` leaves out.
    """
    sections = _language_sections()

    for phase in PHASES:
        composed = prompt(phase)
        given = set(_PHASE_VOCABULARY[phase])
        for heading in _LANGUAGE_HEADINGS:
            if heading in given:
                continue
            assert sections[heading] not in composed, (phase, heading)


def test_no_phase_prompt_defines_a_term_language_md_defines():
    """
    `language.md`'s own headings name what it defines — the closed
    vocabulary and the sentence rules. None of the five phase files, read
    off disk rather than through `prompt()`, may carry one of those headings
    a second time: that is the second copy of a closed list this epic exists
    to remove.
    """
    headings = [line for line in _language_md().splitlines()
               if line.startswith("## ")]

    for phase in PHASES:
        with open(os.path.join(PROMPTS_DIR, phase + ".md"), encoding="utf-8") as f:
            text = f.read()
        for heading in headings:
            assert heading not in text, (phase, heading)


def test_design_never_names_its_own_artefact_a_model():
    """
    Design's own artefact had four names across the five prompts — `model`
    25 times in `design.md`, `description` 8, `design` 5, and
    `what_each_entry_says` in `execute.md`. `description` is what every
    other phase already calls it; `design` and `what_each_entry_says` stay
    as the JSON keys the code reads. `design.md` is the one file that used
    `model` for the same concept, and stops — except the one quoted example
    of a user naming a *revision* in their own words, "the model from Phase
    1", which is the physical part the Intent names, not the artefact
    Design writes about it.
    """
    with open(os.path.join(PROMPTS_DIR, "design.md"), encoding="utf-8") as f:
        text = f.read()

    import re
    found = [line for line in text.splitlines() if re.search(r"\bmodel\b", line, re.IGNORECASE)]

    naming_a_revision = ['"the model from Phase 1"']
    offenders = [line for line in found
                if not any(phrase in line for phrase in naming_a_revision)]

    assert not offenders, offenders
