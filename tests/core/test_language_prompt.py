"""
`config/prompts/language.md` is the single home of the vocabulary every
phase writes and reads: the closed `kind` and `op`
lists, the noun-to-verb map, and the rule that an artefact is written in
English, one heading per closed list. `core.loop.prompt` composes each
phase from `_PHASE_VOCABULARY`'s sections for it, not the whole file — a
phase's own prompt file never repeats a term any section defines.
"""
from core.loop.prompt import ASSERTION_PHASE, DESIGN_PHASE, EXECUTE_PHASE, PLAN_PHASE, REVIEW_PHASE, _PHASE_VOCABULARY, _language_sections, prompt
from core.loop.prompt import _LANGUAGE_HEADINGS
from core.prompts import read_prompt

PHASES = (DESIGN_PHASE, PLAN_PHASE, EXECUTE_PHASE, ASSERTION_PHASE, REVIEW_PHASE)


def _language_md():
    return read_prompt("language.md").strip()


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
        text = read_prompt(phase + ".md")
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
    text = read_prompt("design.md")

    import re
    found = [line for line in text.splitlines() if re.search(r"\bmodel\b", line, re.IGNORECASE)]

    naming_a_revision = ['"the model from Phase 1"']
    offenders = [line for line in found
                if not any(phrase in line for phrase in naming_a_revision)]

    assert not offenders, offenders


def _reply_skeleton(phase):
    """
    The tail of the prompt showing what a `{"declare": ...}` turn carries
    (the reply format was rewritten from a single JSON reply to this
    multi-turn agent's `declare` shape, but the marker check below is the
    same: what the skeleton shows is what a real reply must carry). `## How
    you work` is that section's own heading in every rewritten prompt, so
    the whole section -- every `declare` example a phase shows, not only the
    last -- is what gets checked for the phase's own marker.
    """
    text = read_prompt(phase + ".md")
    return text[text.rfind("## How you work"):]


def test_a_reply_shows_the_marker_language_md_declares_for_its_own_phase():
    """
    `core.design.merge()`'s closed-kind check fires only when a
    Design reply carries `"kind_vocabulary": "closed"` — the marker
    `language.md`'s own "Naming from a closed vocabulary" section says
    every reply carries. `plan.md`'s reply skeleton already shows
    `op_vocabulary` this way; `design.md`'s must show `kind_vocabulary` the
    same way, or the check it switches on is asked for only in prose.

    The markers checked come off `language.md` itself, via `_language_sections`
    and a regex over each section's own text — not a list of markers kept
    here to go stale against it.
    """
    import re

    marker = re.compile(r'"(\w+_vocabulary)":\s*"closed"')
    sections = _language_sections()

    for phase in (DESIGN_PHASE, PLAN_PHASE):
        skeleton = _reply_skeleton(phase)
        for heading in _PHASE_VOCABULARY[phase]:
            for name in marker.findall(sections[heading]):
                assert '"%s"' % name in skeleton, (
                    "%s's reply skeleton does not show %r, which language.md "
                    "declares every reply of its phase carries" % (phase, name))


def test_no_prompt_shows_a_kind_off_the_closed_list():
    """
    A `kind` written into an example is what the phase copies. `design.md`
    showed the outer contour as `"kind": "obround (capsule) outer profile,
    150 long by 50 tall, ends R25"` — a shape word and two dimensions, both
    of which `language.md` refuses ("an outline or a contour is a `pad`", "a
    dimension the drawing gives ... is never in `kind`"). The five runs over
    `mm2023-phase1.pdf` split on it: the one that copied the example kept the
    left end R7 and the right R12.5, and every run that obeyed the closed
    list collapsed both to one `radius` and mirrored the end it read onto the
    end it did not.

    The list is read out of `language.md`, so closing a kind there closes it
    here without this test being touched.
    """
    import re

    closed = re.search(r"kind is one of \(([^)]*)\)", _language_md(), re.S)
    kinds = {word.strip() for word in closed.group(1).replace("\n", " ").split(",")}

    for phase in PHASES:
        text = read_prompt(phase + ".md")
        for shown in re.findall(r'"kind":\s*"([^"]*)"', text):
            assert shown in kinds, (phase, shown)


def test_plan_and_execute_are_told_how_a_bend_is_built_and_design_is_not_charged():
    """
    A bent sheet part is built from walls and bends: a Pad per wall in its
    own plane and a Revolve per bend. Plan and Execute write ops, so they
    carry how; Design never writes one and pays nothing for it.
    """
    for phase in (PLAN_PHASE, EXECUTE_PHASE):
        composed = prompt(phase)
        assert "`revolve`" in composed and "bend allowance" in composed, phase
        assert "own plane" in composed, phase
    assert "bend allowance" not in prompt(DESIGN_PHASE)
