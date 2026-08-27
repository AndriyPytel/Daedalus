"""
FCC-245: measures the ticket's own premise before building anything, and its
own AC 1 kills the ticket -- so this test is the whole deliverable, not a
prelude to one.

**The count.** The ticket counted 26 zero-volume-change failures out of the
245 `preflight_failed` events in `logs/` (245 is right -- every
`preflight_failed` event under `logs/`, sessions and `ab-rlm-agentic/`
alike). 26 was wrong: it came from grepping tracebacks for phrases that also
match `AssertionError`s the model writes into its own Step code ("removed no
material"), a different check with overlapping words, not
`preflight_worker.py::preflight`'s own volume-neutral `RuntimeError`. Counted
by `kind: "preflight_failed"` and the exact `RuntimeError` text, the real
number is 12, across sessions `b31fc95ae4de442fb6ca454b7be68f57` (4),
`c7d892a556624c57b31b508362be4b4b` (1), `549e6232c9e04b659e5a2d536354bd7a`
(2), `141a41df15544013a9740a45138fa7ad` (3), `57af6c09950047bf9f0582cd398d2a66`
(1), and the A/B corpus's `ab-rlm-agentic/sonnet/mm24-off.jsonl` (1).

**The replay.** Each of the 12 was replayed for real: every Step the session
applied before the failure, run in order against a fresh `PartDesign::Body`
under `freecadcmd` (`/Applications/FreeCAD.app/Contents/Resources/bin/
freecadcmd`, FreeCAD 1.1.3), then the failing Step's own code with
`Reversed` flipped (`True`->`False` or `False`->`True`, or appended if the
Step never set it), then the volume compared the same way
`preflight_worker.py::_features_that_moved_no_material` does. This is what
`tests/recorded/preflight_volume_neutral_failures.json` freezes -- a `logs/`
measurement, plus a live FreeCAD run, this suite cannot redo on every call.

**The result.** Only 3 of 12 (25%) move volume when `Reversed` is flipped.
The other 9 fail identically either way: the profile does not overlap the
solid at all, at either sign of `Reversed` -- a placement error, not a
direction error. A quarter is a small fraction by the ticket's own "if the
fraction is small, the ticket is wrong" test: AC 2-4 (a deterministic
Reversed-flip retry on the FCC-239 path) are not built.

**The follow-up this measurement produced, not built here.** The 9 failures
that do not move under either `Reversed` value still reach the model
quoting `preflight_worker.py`'s own trap text -- "A Pocket travels along the
*negative* of its sketch normal unless Reversed is True" -- a diagnosis this
measurement shows is wrong three times out of four. The two failure shapes
(wrong-side vs. no-overlap-at-all) are exactly what this measurement
decided, one flip and one volume compare, before ever asking the model; a
ticket to decide it at the point the check fires and give each shape its
own message is the actual fix this ticket's investigation surfaced.
"""
import json
import os

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "recorded", "preflight_volume_neutral_failures.json")


def _fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def test_the_245_preflight_failure_total_matches_the_ticket():
    data = _fixture()
    assert data["measured_from"]["preflight_failed_events_total_in_logs"] == 245


def test_the_same_check_occurs_12_times_not_26():
    data = _fixture()
    assert data["measured_from"]["same_check_occurrences"] == 12
    assert len(data["cases"]) == 12


def test_only_a_quarter_would_be_fixed_by_flipping_reversed():
    data = _fixture()
    fixed = [c for c in data["cases"] if c["reversed_flip_changed_volume"]]
    unfixed = [c for c in data["cases"] if not c["reversed_flip_changed_volume"]]
    assert len(fixed) == 3
    assert len(unfixed) == 9


def test_every_case_is_a_pocket_and_every_case_was_actually_replayed():
    data = _fixture()
    for case in data["cases"]:
        assert case["typeid"] == "PartDesign::Pocket"
        assert "before_volume_mm3" in case
        assert "after_volume_with_reversed_flipped_mm3" in case
        assert isinstance(case["reversed_flip_changed_volume"], bool)
