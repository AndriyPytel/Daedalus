"""
FCC-241: marker compliance is an instrument, not a gate. FCC-236 changed
`config/prompts/design.md` to raise the rate a Design reply carries
`kind_vocabulary`; before this file, nothing in the suite could say whether
it worked, because the only rate ever measured lived in a docstring
(`FCC-241`'s ticket text) and a docstring is not re-run.

This test states the two rates FCC-241's ticket names as a measured fact,
read off `tests/recorded/vocabulary_marker_compliance.json`
(`tests/recorded/extract_vocabulary_compliance.py` regenerates it, and
names both sessions and the method in its own docstring). It asserts the
fixture's own frozen counts, not a threshold: both sessions predate
FCC-236's own commit (`90113ab`, 2026-08-28 00:50:07) reaching the branch
each ran on, so both numbers are the *before* rate, not an after -- there is
no post-FCC-236 session in `logs/` yet to build an after-rate from. A test
that asserted "the rate must exceed N%" would start failing not when
compliance regressed but the day a genuinely post-FCC-236 session finally
lands here with a different rate, which is backwards for an instrument.

The markers checked are derived from `config/prompts/language.md` itself,
the same way `tests/test_language_prompt.py::test_a_reply_shows_the_marker_
language_md_declares_for_its_own_phase` derives them -- via
`_language_sections()`, `_PHASE_VOCABULARY` and a regex over each phase's
own sections -- not a copy of `{"design": "kind_vocabulary", "plan":
"op_vocabulary"}` kept a second time in this file.
"""
import json
import os
import re

from core.loop import DESIGN_PHASE, PLAN_PHASE, _PHASE_VOCABULARY, _language_sections

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "recorded", "vocabulary_marker_compliance.json")

MARKER = re.compile(r'"(\w+_vocabulary)":\s*"closed"')


def _fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def _declared_markers():
    sections = _language_sections()
    declared = {}
    for phase, name in ((DESIGN_PHASE, "design"), (PLAN_PHASE, "plan")):
        names = [n for heading in _PHASE_VOCABULARY[phase]
                 for n in MARKER.findall(sections[heading])]
        assert len(names) == 1, (phase, names)
        declared[name] = names[0]
    return declared


def test_the_fixture_checks_the_markers_language_md_declares():
    data = _fixture()
    assert data["markers_by_phase"] == _declared_markers()


def test_afa0142_the_ticket_own_by_hand_session_measures_33_and_43_percent():
    session = _fixture()["sessions"]["afa0142baef847b0a9cd9139390af993"]
    design = session["phases"]["design"]
    plan = session["phases"]["plan"]
    assert (design["replies"], design["carried_marker"]) == (9, 3)
    assert (plan["replies"], plan["carried_marker"]) == (7, 3)
    assert round(100 * design["carried_marker"] / design["replies"]) == 33
    assert round(100 * plan["carried_marker"] / plan["replies"]) == 43


def test_480b9995_after_the_epic_but_still_pre_fcc_236_measures_40_and_100_percent():
    """
    Plan's `n=1` here: one Plan call in the whole session, and it carried
    the marker -- 100% of one is not evidence a fix worked, only a data
    point this fixture states rather than hides.
    """
    session = _fixture()["sessions"]["480b9995421e443d97b439a4f55bc3fe"]
    design = session["phases"]["design"]
    plan = session["phases"]["plan"]
    assert (design["replies"], design["carried_marker"]) == (5, 2)
    assert (plan["replies"], plan["carried_marker"]) == (1, 1)
    assert round(100 * design["carried_marker"] / design["replies"]) == 40
    assert plan["carried_marker"] / plan["replies"] == 1.0


def test_neither_recorded_session_postdates_fcc_236():
    """
    FCC-236 committed 2026-08-28 00:50:07 (`90113ab`). Both sessions this
    fixture measures started before that, so both are *before* rates --
    named in the fixture's own `"when"` field, not left to be assumed.
    """
    data = _fixture()
    for when in (s["when"] for s in data["sessions"].values()):
        assert "before" in when
