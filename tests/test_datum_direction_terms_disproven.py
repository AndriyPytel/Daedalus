"""
FCC-242's own measurement, kept as a test because it is the reason the ticket
closed `wontfix` rather than delivering a Gate check.

The premise was: a datum name is a term now that FCC-234 (`config/prompts/
language.md`, landed 2026-08-27) closed the vocabulary, so two claims naming
different datums for the same thing can be compared by term rather than by
prose similarity. The evidence was session `480b9995421e443d97b439a4f55bc3fe`
Step 8, which cites `hole-right -> axis` (the acceptance line reads "...from
the bottom face to the top face") and `counterbore-right -> position -> the
bottom face`, while its own prose calls the entry point "the lower ear's
underside at Z = 10 mm" and never writes either cited datum term. Step 6, the
left-hand twin of the same two features, writes "the bottom face" in its own
prose and both of its claims held at Assertion; Step 8's did not.

Three widths of "does the Step's own prose name the datum its citation
names" were measured against `tests/recorded/datum_satisfies_steps.json`
(built from `logs/sessions/`, every session of which predates `language.md`
— so none of these numbers can be read as the false-positive rate of a Plan
actually held to the closed list, only as what the mechanism does to the
corpus available; the script that built it is gone):

- whole-Step, one bag of terms per side: 10 of 231 comparable Steps raised,
  and it MISSES the evidence Step, because that Step's other, correct
  citations ("the right end face", "the longitudinal centre plane") share
  the bag with the wrong pair and mask it.
- per-citation, any field position/axis/plane names: 71 of 495 entries
  (14%) raised. Reading them by hand, almost all are a Step giving one
  coordinate as a bare number without repeating its datum's name, while
  naming a *different* datum for a *different* coordinate in the same
  sentence — not a disagreement, the ordinary way a Step fixes a point in
  three axes.
- per-citation, restricted to `axis`/`plane` fields (`core.loop`'s own
  `_two_directions_in_one_step` / `_A_DIRECTION_FIELD` scope, which already
  excludes `position` for the reason above): 12 of 80 raised. Read by hand,
  10 of the 12 are not a second kind of noise — they are the first kind's
  underlying mistake, restated: "perpendicular to the top face" and
  "perpendicular to the bottom face" name THE SAME axis, because the two
  faces are parallel, so a Step naming one of a pair of parallel faces for
  its direction is not wrong because its own prose also names the other one
  for an unrelated coordinate in the same sentence. A direction has many
  true names; comparing datum NAMES to check a DIRECTION cannot work at any
  threshold, and no narrower version of this rule rescues it, because the
  premise — that a direction has one true name the way a datum has one
  true name — is false. One of the ten also happens to use "the boss top
  face", a compound term this pre-`language.md` session invented, off the
  eleven-name closed list by construction.

What survived is not a narrower version of the same rule: "from the bottom
face to the top face" names two ENDPOINTS, not a direction, and an endpoint
has one true name the way a datum does. Restricted to that one phrasing, the
corpus has exactly four comparable entries, and the check separates all four
correctly: the two real disagreements are exactly Step 8's two claims, and
the two it correctly leaves alone are Step 6's — same two features, same
axis wording, the one difference being that Step 6's own prose writes "the
bottom face" and Step 8's does not. Four is not enough to wire into a Gate
that runs on every Plan; it is what says the mechanism is sound for entry
faces specifically, not merely lucky once.

This defect is not currently uncaught. `Plan.satisfies` threads a Step's
citations into `assertion()` (`core/loop.py:1270`, `core/loop.py:2416`),
which evaluates the acceptance list exactly, no heuristic, against the
finished Document — which is how the evidence session's own Assertion phase
found both of Step 8's claims false. The open question was only ever whether
an entry-face disagreement like this one could be caught before Execute
spends a Step on it rather than after every Step is spent; this measurement
answers "not by comparing datum names at the Plan Gate" and leaves the
scheduling question to whatever ticket takes on `assertion()` running once,
at the end, rather than address by address as each Step lands.
"""
import json
import os
import re

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recorded",
                       "datum_satisfies_steps.json")
LANGUAGE_MD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "config", "prompts", "language.md")

SATISFIES = re.compile(r"^(?P<do>.*?)\s*\(satisfies:\s*(?P<sat>.*)\)\s*$", re.DOTALL)
FLAG = re.compile(r"\s*\((?:read|derived|assumed)\)$")
FROM_TO = re.compile(r"from (the [a-z ]+?) to (the [a-z ]+?)(?:[.,;]|$)")


def _datum_vocabulary():
    """The eleven datum names, read off `language.md` rather than copied."""
    text = open(LANGUAGE_MD, encoding="utf-8").read()
    listed = re.search(r"Datum `name`: one of \(([^)]*)\)", text).group(1)
    return [term.strip() for term in listed.split(",")]


def _addressed(acceptance):
    out = {}
    for entry in acceptance:
        address, _, value = entry.partition(": ")
        if value:
            out[" ".join(address.split())] = value.strip()
    return out


def _split_step(step):
    matched = SATISFIES.match(step)
    if not matched:
        return step.strip(), []
    do = matched.group("do").strip()
    cited = [c.strip() for c in matched.group("sat").split(", ") if c.strip()]
    return do, cited


def _datums_in(text, datums):
    return {d for d in datums if d in text}


def _direction_field(address):
    parts = [p.strip() for p in address.split("→")]
    return len(parts) == 3 and parts[0] == "features" and parts[2] in ("axis", "plane")


def _fixture():
    return json.load(open(FIXTURE, encoding="utf-8"))["plans"]


def test_the_fixture_keeps_what_vocabulary_steps_json_stripped():
    plans = _fixture()
    assert len(plans) == 68
    steps = [step for plan in plans for step in plan["steps"]]
    assert len(steps) == 592
    assert sum(1 for step in steps if "(satisfies:" in step) == 398


def test_whole_step_one_bag_of_terms_misses_the_evidence_step():
    datums = _datum_vocabulary()
    plans = _fixture()
    comparable = 0
    raised = 0
    evidence_raised = False
    for plan in plans:
        of = _addressed(plan["acceptance"])
        for index, step in enumerate(plan["steps"]):
            do, cited = _split_step(step)
            do_datums = _datums_in(do, datums)
            cited_datums = set()
            for citation in cited:
                address = FLAG.sub("", citation)
                for datum in datums:
                    if address.endswith(datum):
                        cited_datums.add(datum)
                cited_datums |= _datums_in(of.get(address, ""), datums)
            if not cited_datums:
                continue
            comparable += 1
            if do_datums and do_datums.isdisjoint(cited_datums):
                raised += 1
                if plan["session"] == "480b9995421e443d97b439a4f55bc3fe" and index == 7:
                    evidence_raised = True

    assert comparable == 231
    assert raised == 10
    assert evidence_raised is False


def test_per_citation_any_field_finds_the_evidence_but_is_too_noisy():
    datums = _datum_vocabulary()
    plans = _fixture()
    comparable = 0
    raised = 0
    evidence_hits = set()
    for plan in plans:
        of = _addressed(plan["acceptance"])
        for index, step in enumerate(plan["steps"]):
            do, cited = _split_step(step)
            do_datums = _datums_in(do, datums)
            for citation in cited:
                address = FLAG.sub("", citation)
                entry_datums = {d for d in datums if address.endswith(d)}
                entry_datums |= _datums_in(of.get(address, ""), datums)
                if not entry_datums:
                    continue
                comparable += 1
                if entry_datums & do_datums:
                    continue
                if do_datums:
                    raised += 1
                    if plan["session"] == "480b9995421e443d97b439a4f55bc3fe" and index == 7:
                        evidence_hits.add(address)

    assert comparable == 495
    assert raised == 71
    assert evidence_hits == {"features → hole-right → axis",
                             "features → counterbore-right → axis",
                             "features → counterbore-right → position → the bottom face"}


def test_axis_and_plane_only_still_fires_on_parallel_faces():
    """
    Restricting to the fields `_two_directions_in_one_step` already trusts
    for a direction does not fix the mechanism: 10 of the 12 it raises are
    a direction with two simultaneously true names, not a disagreement.
    """
    datums = _datum_vocabulary()
    plans = _fixture()
    comparable = 0
    raised = []
    for plan in plans:
        of = _addressed(plan["acceptance"])
        for index, step in enumerate(plan["steps"]):
            do, cited = _split_step(step)
            do_datums = _datums_in(do, datums)
            for citation in cited:
                address = FLAG.sub("", citation)
                if not _direction_field(address):
                    continue
                entry_datums = _datums_in(of.get(address, ""), datums)
                if not entry_datums:
                    continue
                comparable += 1
                if entry_datums & do_datums:
                    continue
                if do_datums:
                    raised.append((plan["session"], index, address))

    assert comparable == 80
    assert len(raised) == 12
    assert sum(1 for session, index, _ in raised
              if session == "480b9995421e443d97b439a4f55bc3fe" and index == 7) == 2


def test_from_x_to_y_names_two_endpoints_and_survives_at_n_equals_two():
    """
    The one candidate that is not the direction mistake restated: "from A to
    B" names where a feature starts and ends, and a start has one true name
    the way a datum does. Four entries in the whole corpus use this phrasing
    for an `axis`; the check separates all four correctly, and the two real
    disagreements it finds are exactly the evidence Step's two claims.

    Four is a demonstration that the mechanism is sound for entry faces, not
    a sample size a Gate check could be wired on — `logs/` has one Plan that
    happens to phrase an axis this way twice, over its two mirrored Steps.
    """
    datums = _datum_vocabulary()
    plans = _fixture()
    entries = []
    for plan in plans:
        of = _addressed(plan["acceptance"])
        for index, step in enumerate(plan["steps"]):
            do, cited = _split_step(step)
            for citation in cited:
                address = FLAG.sub("", citation)
                if not _direction_field(address):
                    continue
                value = of.get(address, "")
                for start, end in FROM_TO.findall(value):
                    start, end = start.strip(), end.strip()
                    if start not in datums or end not in datums:
                        continue
                    disagrees = start not in do and end not in do
                    entries.append((plan["session"], index, address, disagrees))

    assert len(entries) == 4
    raised = {(session, index, address) for session, index, address, disagrees in entries
             if disagrees}
    assert raised == {
        ("480b9995421e443d97b439a4f55bc3fe", 7, "features → hole-right → axis"),
        ("480b9995421e443d97b439a4f55bc3fe", 7, "features → counterbore-right → axis"),
    }
    held = {(session, index, address) for session, index, address, disagrees in entries
           if not disagrees}
    assert held == {
        ("480b9995421e443d97b439a4f55bc3fe", 6, "features → hole-left → axis"),
        ("480b9995421e443d97b439a4f55bc3fe", 6, "features → counterbore-left → axis"),
    }
