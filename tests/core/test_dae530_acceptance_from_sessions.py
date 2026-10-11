"""
The three Acceptance Criteria of the edit runs, computed from sessions copied into `logs/dae530-*/`.

The session list is whatever `*.jsonl` files sit under those directories; a session replayed before the change is not copied there. A run whose file name starts `202.` is a part-202 run. With no session there the measurement is skipped, and with sessions it needs three part-202 runs.
"""
import glob
import os
import re

import pytest

from core.design.core import UNDER
from core.design.gaps import entries
from core.design.merge import _the_plane_it_names
from core.design.settlement_checks import described_twice
from core.events import ASSERTION, DESIGN, replay
from core.loop.plan import _acceptance_of

LOGS = os.path.join(os.path.dirname(__file__), "..", "..", "logs")


def sessions(logs=LOGS):
    return sorted(glob.glob(os.path.join(logs, "dae530-*", "**", "*.jsonl"), recursive=True))


def _final_design(path):
    events, _ = replay(path)
    designs = [event for event in events if event.kind == DESIGN]
    base = next((event.data["the_base_part"] for event in reversed(designs)
                 if event.data.get("the_base_part")), "")
    return (designs[-1].data.get("model") or {}) if designs else {}, base, \
        [event.data for event in events if event.kind == ASSERTION]


def _twice(the_part):
    return [(a.get("id"), b.get("id")) for a, b, _ in described_twice(the_part)]


def _entries_of(feature, lines):
    return [line for line in lines if line.startswith("features%s%s%s" % (UNDER, feature, UNDER))]


def pairs_checked(path):
    """AC1: the features of a pair described twice with differing values that still have an acceptance entry."""
    the_part, _, _ = _final_design(path)
    lines = _acceptance_of(the_part)
    return [feature for pair in _twice(the_part) for feature in pair if _entries_of(feature, lines)]


def pairs_failed_or_unnamed(path):
    """AC2: a pair that failed Assertion on one of its entries, or that no Assertion report names."""
    the_part, _, assertions = _final_design(path)
    unmet = [str(entry) for data in assertions for entry in data.get("unmet") or ()]
    reported = " ".join(str(line) for data in assertions for line in data.get("left_out") or ())
    return [feature for pair in _twice(the_part) for feature in pair
            if any(("features%s%s%s" % (UNDER, feature, UNDER)) in entry for entry in unmet)
            or feature not in reported]


def names_that_are_not_addresses(path):
    """AC3: datums and feature ids that are no address in the base part, and planes named by more than one datum."""
    the_part, base, _ = _final_design(path)
    offered = {" ".join(line.split("(area")[0].split()) for line in base.splitlines()}
    datums = [str(datum.get("name")) for datum in the_part.get("datums") or () if isinstance(datum, dict)]
    ids = [str(feature.get("id")) for feature in the_part.get("features") or () if isinstance(feature, dict)]
    planes = [_the_plane_it_names(name) or name for name in datums]
    return ([name for name in datums if " ".join(name.split()) not in offered]
            + [name for name in ids if " ".join(name.rpartition("@")[2].split()) not in offered]
            + sorted({plane for plane in planes if planes.count(plane) > 1}))


def _part_202(paths):
    return [path for path in paths if os.path.basename(path).startswith("202.")]


def test_the_edit_runs_after_the_change_meet_the_three_acceptance_criteria():
    paths = sessions()
    if not paths:
        pytest.skip("no session under logs/dae530-*/ yet")
    assert len(_part_202(paths)) >= 3

    assert {path: pairs_checked(path) for path in _part_202(paths) if pairs_checked(path)} == {}
    assert {path: pairs_failed_or_unnamed(path) for path in paths if pairs_failed_or_unnamed(path)} == {}
    assert {path: names_that_are_not_addresses(path) for path in _part_202(paths)
            if names_that_are_not_addresses(path)} == {}


def _a_session(tmp_path, name, model, base, assertion):
    import json
    path = tmp_path / "dae530-x" / "opus" / name
    path.parent.mkdir(parents=True)
    path.write_text("\n".join(json.dumps(event) for event in [
        {"kind": "design", "at": 1, "data": {"model": model, "the_base_part": base}},
        {"kind": "assertion", "at": 2, "data": assertion}]))
    return str(path)


_BASE = "The part before the edit, in mm:\nplane y = -17.5 (area 1605 mm2)\nplane y = -177.5 (area 900 mm2)"


def _bosses(a, b):
    def boss(identity, diameter):
        return {"id": identity, "kind": "boss", "diameter": {"value": diameter, "flag": "read"},
                "position": {"plane y = -177.5": {"value": "10 mm", "flag": "read"}}}
    return {"datums": [{"name": "plane y = -177.5"}], "features": [boss(a, "110 mm"), boss(b, "100 mm")]}


def test_the_measurements_read_a_pair_left_out_and_names_that_are_addresses(tmp_path):
    path = _a_session(tmp_path, "202.1.jsonl", _bosses("boss @ plane y = -17.5", "pin @ plane y = -17.5"),
                      _BASE, {"unmet": [], "left_out": ["boss @ plane y = -17.5 and pin @ plane y = -17.5"]})

    assert pairs_checked(path) == [] and pairs_failed_or_unnamed(path) == []
    assert names_that_are_not_addresses(path) == []


def test_the_measurements_find_a_pair_that_failed_names_off_the_base_and_one_plane_named_twice(tmp_path):
    part = _bosses("boss-centre", "boss-left")
    part["datums"].append({"name": "plane y=-177.50"})
    path = _a_session(tmp_path, "202.1.jsonl", part, _BASE,
                      {"unmet": ["features \u2192 boss-centre \u2192 diameter: 110 mm"], "left_out": []})

    assert set(pairs_failed_or_unnamed(path)) == {"boss-centre", "boss-left"}
    assert set(names_that_are_not_addresses(path)) >= {"boss-centre", "boss-left", "plane y = -177.5"}
