"""
Level 1 — the merge and the rendering of the part Design describes.

No provider, no FreeCAD: the merge is the single most load-bearing piece of
logic in FCC-93, so it is tested on its own rather than only through a run. The
numbers are the ones from the run that named the defect
(`logs/sessions/daefe57b593448e9baf83422fc37af1e.jsonl`, mm25-p1-1.pdf).
"""
import json
import os

from core import design
from core.design import (
    UNDER, corrected, crosswise, decided_for_you, distilled, each_disagreement_once,
    entries, entry_at, grounded, merge, read_off_the_wrong_view, render, said_at,
    settled_by_the_tracing, where_the_direction_is,
)


def _read(value):
    return {"value": value, "flag": "read"}


DEPTH_OFF_THE_SECTION = {
    "envelope": {"depth front to rear": _read("100 mm")},
    "features": [{"id": "hole-40-left", "kind": "Ø40 hole", "topology": "through",
                  "axis": _read("front to rear, perpendicular to the front face"),
                  "position": {"the left end face": _read("20 mm")},
                  "seen_in": ["front view"]}],
}


def test_a_field_already_filled_is_not_replaced():
    """
    The 100 mm read off SECTION B-B was not overruled or argued with in the run
    that named this defect — it was simply gone, and the Plan said 150 × 50 × 50.
    A later source may not write over a value, whatever it thinks it saw.
    """
    grown = merge(DEPTH_OFF_THE_SECTION,
                  {"envelope": {"depth front to rear": _read("50 mm")}})

    assert grown["envelope"]["depth front to rear"] == _read("100 mm")


def test_the_disagreement_is_recorded_rather_than_reconciled():
    """
    Two incompatible dimensions stay recorded as a conflict and are not settled
    by the machine — the FCC-59 boundary. The known cost is accepted: the first
    pass is authoritative even where it was wrong, so the disagreement has to
    reach the user at the Plan Gate instead.
    """
    grown = merge(DEPTH_OFF_THE_SECTION,
                  {"features": [{"id": "hole-40-left",
                                 "position": {"the left end face": _read("25 mm")}}]})

    held = grown["features"][0]["position"]["the left end face"]
    assert held == _read("20 mm"), "the later pass overwrote a value that was already read"

    conflict = grown["conflicts"][0]
    assert "hole-40-left" in conflict["about"] and "the left end face" in conflict["about"]
    assert conflict["stands"] == _read("20 mm")
    assert conflict["was_also_said"] == _read("25 mm")


def test_an_empty_field_is_filled_and_a_new_feature_is_added():
    """The model grows. Merge-only would be useless if it only ever refused."""
    grown = merge(DEPTH_OFF_THE_SECTION, {
        "features": [
            {"id": "hole-40-left", "position": {"the top face": _read("25 mm")},
             "seen_in": ["SECTION B-B"]},
            {"id": "hole-35", "kind": "Ø35 hole", "topology": "through",
             "axis": _read("front to rear")},
        ],
    })

    left = grown["features"][0]
    assert left["position"] == {"the left end face": _read("20 mm"),
                                "the top face": _read("25 mm")}
    assert left["seen_in"] == ["front view", "SECTION B-B"]
    assert [feature["id"] for feature in grown["features"]] == ["hole-40-left", "hole-35"]
    assert "conflicts" not in grown, "filling an empty field is not a disagreement"


def test_features_are_matched_by_id_and_not_by_position_in_the_list():
    """
    The rule is enforced in the loop by `id`, not only in the prompt: a fragment
    that names one feature must not land on whichever one happens to be first.
    """
    grown = merge({"features": [{"id": "a", "kind": "boss"}, {"id": "b"}]},
                  {"features": [{"id": "b", "kind": "pocket"}]})

    assert grown["features"] == [{"id": "a", "kind": "boss"}, {"id": "b", "kind": "pocket"}]


def test_a_datum_keeps_the_name_it_was_first_given():
    grown = merge({"datums": [{"name": "the left end face", "is": "the flat left face"}]},
                  {"datums": [{"name": "the left end face", "is": "somewhere else"},
                              {"name": "the ring axis", "is": "the Ø40 bore axis"}]})

    assert grown["datums"][0]["is"] == "the flat left face"
    assert [datum["name"] for datum in grown["datums"]] == ["the left end face",
                                                            "the ring axis"]
    assert grown["conflicts"][0]["stands"] == "the flat left face"


def test_what_is_not_defined_accumulates_without_repeating_itself():
    entry = {"what": "the general tolerance", "looked_in": ["the notes"]}
    grown = merge(merge({}, {"not_defined": [entry]}), {"not_defined": [entry]})

    assert grown["not_defined"] == [entry]


NOT_DEFINED_DEPTH = {
    "envelope": {"depth front to rear": {"value": "not defined", "flag": "not_defined"}},
}


def test_a_not_defined_field_is_filled_by_a_later_pass():
    """
    FCC-235: session afa0142baef847b0a9cd9139390af993 derived a depth its own
    first pass had recorded `not_defined` and could not write it — the field
    was already "filled", by the general rule, with a gap rather than a value.
    `not_defined` -> a value is the one exception to that rule: it fills, and
    is not a `conflicts` entry.
    """
    grown = merge(NOT_DEFINED_DEPTH,
                  {"envelope": {"depth front to rear":
                                {"value": "50.2 mm", "flag": "derived",
                                 "from": "SECTION A-A's body was placed as a picture, "
                                         "so its outline was traced"}}})

    assert grown["envelope"]["depth front to rear"] == \
        {"value": "50.2 mm", "flag": "derived",
         "from": "SECTION A-A's body was placed as a picture, so its outline was traced"}
    assert not grown.get("conflicts")


def test_a_filled_field_still_cannot_be_replaced_even_by_not_defined():
    """
    `not_defined` -> value is the *only* exception. A field already carrying a
    real value is unmoved by a later pass, whether that pass offers another
    value or claims the field is now not defined.
    """
    grown = merge(DEPTH_OFF_THE_SECTION,
                  {"envelope": {"depth front to rear":
                                {"value": "not defined", "flag": "not_defined"}}})

    assert grown["envelope"]["depth front to rear"] == _read("100 mm")
    assert grown["conflicts"][0]["stands"] == _read("100 mm")


def test_a_fragment_that_is_not_a_model_leaves_the_part_as_it_was():
    """
    Design answering with prose is a phase that did not do its job, and losing
    the model over it would be worse than growing nothing. The empty answer and
    the unusable one are the same case, the way an empty acceptance list is.
    """
    assert merge(DEPTH_OFF_THE_SECTION, "a 150 x 50 x 50 block") == DEPTH_OFF_THE_SECTION
    assert merge(DEPTH_OFF_THE_SECTION, None) == DEPTH_OFF_THE_SECTION
    assert merge(None, {"features": []}) == {"features": []}


def test_merging_does_not_touch_what_it_was_given():
    """The model on the stream is what the Gate showed; growth is a new one."""
    held = {"features": [{"id": "hole-40-left", "kind": "Ø40 hole"}]}
    merge(held, {"features": [{"id": "hole-40-left", "topology": "through"},
                              {"id": "hole-35"}]})

    assert held == {"features": [{"id": "hole-40-left", "kind": "Ø40 hole"}]}


THE_CAPSULE_SAID_IN_PASSING = {
    "datums": [
        {"name": "the left end face",
         "is": "the planar end face at the left of the 150 mm overall length"},
        {"name": "the top face", "is": "the outer face at the top"},
    ],
    "envelope": {"length between the end faces": _read("150 mm"),
                 "height between the top and bottom faces": _read("50 mm")},
    "features": [
        {"id": "left-end-bore", "kind": "one of two Ø40 bores", "topology": "through the depth",
         "axis": _read("front to rear, perpendicular to the top face")},
        {"id": "red-edge-fillets", "kind": "R2 fillets on all edges shown red",
         "radius": _read("2 mm"),
         "edge_selection": [{"what": "the two closed capsule-shaped exterior perimeter loops",
                             "value": "each loop where its outer face meets the exterior top, "
                                      "bottom, and rounded end surfaces",
                             "flag": "read"}]},
    ],
}


def test_an_outside_described_in_passing_and_carried_by_nothing_is_a_conflict():
    """
    The recorded run
    (`logs/sessions/57af6c09950047bf9f0582cd398d2a66.jsonl`, mm25-p1-1.pdf):
    the description called the exterior perimeter capsule-shaped, but said it
    inside the *fillet's* edge selection, where it is prose about which edges to
    round. No feature carried the contour, so a 150 × 100 rectangle was padded
    50 deep and every acceptance check passed on a box.
    """
    grown = merge({}, THE_CAPSULE_SAID_IN_PASSING)
    about = [conflict["about"] for conflict in grown["conflicts"]]

    assert "envelope → the shape of the outside" in about
    said = [conflict for conflict in grown["conflicts"]
            if conflict["about"] == "envelope → the shape of the outside"][0]
    assert "capsule-shaped" in said["was_also_said"]
    assert "red-edge-fillets" in said["was_also_said"], "the conflict does not say where it read it"


def test_a_datum_that_calls_a_rounded_end_flat_is_a_conflict():
    """
    "the planar end face at the left" cannot sit beside "rounded end surfaces",
    and the merge cannot see it: the two were never written under one address.
    """
    grown = merge({}, THE_CAPSULE_SAID_IN_PASSING)
    about = [conflict["about"] for conflict in grown["conflicts"]]

    assert "datums → the left end face" in about
    assert "datums → the top face" not in about, "a face nobody called rounded is not in conflict"


def test_a_feature_that_carries_the_outer_contour_settles_it():
    """The fix the conflict asks for: the outside as geometry, not as prose."""
    grown = merge({}, {
        "datums": [{"name": "the left rounded end",
                    "is": "the semicylindrical end at the left"}],
        "envelope": {"length between the end faces": _read("150 mm")},
        "features": [{"id": "outer-profile",
                      "kind": "obround (capsule) outer profile, 150 x 50, ends R25",
                      "plane": _read("the plane of the length and the height"),
                      "topology": "extruded 100 mm front to rear"}],
    })

    assert "conflicts" not in grown


def test_a_part_with_a_boxed_outside_raises_nothing():
    """Silence on the ordinary part, or the finding is noise at every Gate."""
    assert "conflicts" not in merge({}, THE_PART_AS_A_BOX)


THE_PART_AS_A_BOX = {
    "datums": [{"name": "the left end face", "is": "the planar end face at the left"}],
    "envelope": {"length between the end faces": _read("150 mm")},
    "features": [{"id": "bore", "kind": "Ø40 bore", "topology": "through",
                  "axis": _read("front to rear, perpendicular to the left end face")}],
}


def test_the_finding_does_not_pile_up_over_a_run():
    """Design is called again and again; one reading is one conflict."""
    grown = merge(merge({}, THE_CAPSULE_SAID_IN_PASSING),
                  {"features": [{"id": "hole-35", "axis": _read("front to rear")}]})

    assert len(grown["conflicts"]) == len(merge({}, THE_CAPSULE_SAID_IN_PASSING)["conflicts"])


def test_a_conflict_the_merge_did_not_see_is_still_not_a_criterion():
    """It holds two readings of which neither is settled, the same as any other."""
    grown = merge({}, THE_CAPSULE_SAID_IN_PASSING)

    assert not [entry for entry in entries(grown) if "the shape of the outside" in entry["about"]]


THE_CLAMP_AS_ONE_FLAT_PROFILE = {
    "datums": [{"name": "the ring axis",
                "is": "the front-to-rear axis common to the Ø50 ring and the Ø42 bore"}],
    "envelope": {"length between the leftmost and rightmost tangent planes": _read("108 mm"),
                 "front-to-rear depth at the sectioned right lug": _read("30 mm")},
    "features": [
        {"id": "right-fastener-hole",
         "kind": "Ø6.6 clearance bore with hexagonal recess for an M6 fastener",
         "topology": "Ø6.6 through all, with an 11 mm hexagonal recess 6 mm deep",
         "position": {"the ring axis": _read("70.5 mm")},
         "seen_in": ["plan view on the sheet", "SECTION A-A"]},
        {"id": "right-rounded-lug-end", "kind": "rounded outer end of the right-hand lug",
         "radius": _read("12.5 mm"),
         "seen_in": ["VIEW B", "plan view on the sheet"]},
    ],
}


def test_a_hole_that_never_says_which_way_its_axis_points_is_a_conflict():
    """
    The recorded run
    (`logs/sessions/8d07a842d06b487d8e7729ecba27ce4b.jsonl`, the 2023 pipe
    clamp): both M6 holes are drawn as circles in the plan, so both axes run
    perpendicular to the ring axis, and SECTION A-A shows the right-hand one
    crossing the split it is there to pinch. Both were built out of one sketch
    in the elevation plane, parallel to the ring axis, and the clamp screw no
    longer crossed the split. Direction had nowhere to be written but
    `topology`, which nothing reads, so the right reading could not be recorded
    and the wrong one could not be contradicted.
    """
    grown = merge({}, THE_CLAMP_AS_ONE_FLAT_PROFILE)
    found = [conflict for conflict in grown["conflicts"]
             if "right-fastener-hole" in conflict["about"]]

    assert found, "a bore was described with no axis and nothing said so"
    assert "axis" in found[0]["about"]
    assert "Ø6.6 clearance bore" in found[0]["was_also_said"], \
        "the conflict does not say what it read the axis off"


def test_a_curve_that_never_says_which_plane_it_lies_in_is_a_conflict():
    """
    The same defect on the other kind of feature, and the prompt already argues
    it in words: rounded ends R12.5 in the plan and R12.5 in the elevation are
    two different parts, and no radius on the sheet decides between them. A
    radius carried alone is a curve facing whichever way the base profile does.
    """
    grown = merge({}, THE_CLAMP_AS_ONE_FLAT_PROFILE)
    found = [conflict for conflict in grown["conflicts"]
             if "right-rounded-lug-end" in conflict["about"]]

    assert found and "plane" in found[0]["about"]


def test_a_direction_the_feature_carries_settles_it_and_is_checked_like_a_length():
    """
    The fix the conflict asks for, and the reason it has to be a field: an axis
    written as a value is an entry like any other, so the acceptance derived
    from the description measures the direction the same way it measures the
    108 mm — named by the datums it is measured against, never as X, Y or Z.
    """
    grown = merge({}, {
        "datums": [{"name": "the ring axis", "is": "the front-to-rear axis of the Ø42 bore"}],
        "features": [
            {"id": "right-fastener-hole", "kind": "Ø6.6 clearance bore for an M6 fastener",
             "axis": _read("perpendicular to the ring axis, crossing the split")},
            {"id": "right-rounded-lug-end", "kind": "rounded outer end of the right-hand lug",
             "radius": _read("12.5 mm"),
             "plane": _read("the plane of the length and the depth front to rear")},
        ],
    })

    assert "conflicts" not in grown
    said = [entry["says"] for entry in entries(grown)]
    assert "features → right-fastener-hole → axis: perpendicular to the ring axis, " \
           "crossing the split (read)" in said


def test_a_feature_with_neither_an_axis_nor_a_curve_is_left_alone():
    """A fillet follows the edges it is put on, and a datum points nowhere at
    all; raising either would be noise at every Gate."""
    grown = merge({}, {
        "datums": [{"name": "the left end face", "is": "the flat face at the left"}],
        "features": [{"id": "red-edge-fillets", "kind": "R2 fillets on all edges shown red",
                      "radius": _read("2 mm")}],
    })

    assert "conflicts" not in grown


def test_a_fillets_own_topology_prose_never_asks_it_for_an_axis():
    """
    FCC-243, session `480b9995421e443d97b439a4f55bc3fe`: `fillet-outer` has
    `kind: "fillet"` and a `topology` reading "...the outer edges of the arms
    and band, the ring's two end rims, the cutout and the slot" — the word
    "slot" put it through the axis check, though `direction`'s own docstring
    says a fillet is deliberately never asked for one. A feature's `kind` is
    the closed vocabulary's own word for what it is; `topology` says what it
    runs into, not what it is, and must never be read for that.
    """
    grown = merge({}, {
        "features": [{"id": "fillet-outer", "kind": "fillet",
                      "topology": "the outer edges of the arms and band, the ring's two "
                                  "end rims, the cutout and the slot"}],
    })

    assert "conflicts" not in grown


THE_FOOT_NOBODY_PLACED = {
    "datums": [
        {"name": "the leftmost tangent plane of the part",
         "is": "the plane tangent to the rounded left end", "fixes": "the length"},
        {"name": "the lower tangent plane of the ring",
         "is": "the horizontal plane tangent to the lowest point of the Ø50 ring",
         "fixes": "the height"},
        {"name": "the ring axis",
         "is": "the front-to-rear axis common to the Ø50 ring and the Ø42 bore",
         "fixes": "the length and the height"},
    ],
    "envelope": {"length between the leftmost and rightmost tangent planes": _read("108 mm"),
                 "height between the uppermost and lowermost extents": _read("50 mm")},
    "features": [
        {"id": "left-arm-and-fork-profile",
         "kind": "left-hand mounting arm joining tangentially to the outer ring",
         "plane": _read("the plane of the length and the height"),
         "height": _read("15 mm"),
         "position": {"the leftmost tangent plane of the part": _read("0 mm"),
                      "the ring axis": _read("30 mm")},
         "seen_in": ["VIEW B", "plan view on the sheet"]},
    ],
}


def test_a_feature_sized_in_a_direction_and_placed_in_none_of_it_is_a_conflict():
    """
    The recorded run
    (`logs/sessions/8d07a842d06b487d8e7729ecba27ce4b.jsonl`, the 2023 pipe
    clamp): the drawing puts the left mounting foot flush with the bottom of the
    ring, 15 mm tall with the ring axis 25 mm above its underside, and it was
    built `arm_top = 32.5`, `arm_bottom = 17.5` — symmetric about that axis.
    Nothing was overruled. The description declared "the lower tangent plane of
    the ring" a datum and never wrote a value against it: the arm carries
    `height: 15 mm`, a length from the left tangent plane and a length to the
    cutout's closed end, and no position in the height at all. The prompt
    predicts the rest — "sensible" defaults to centred — and the acceptance,
    derived from the entries the description holds, had nothing to measure it
    with, so a part wrong in the way the drawing is loudest about came back
    green.
    """
    grown = merge({}, THE_FOOT_NOBODY_PLACED)
    found = [conflict for conflict in grown["conflicts"]
             if "left-arm-and-fork-profile" in conflict["about"]]

    assert found, "a feature 15 mm tall was placed nowhere in the height and nothing said so"
    assert found[0]["about"] == \
        "features → left-arm-and-fork-profile → position → the height"
    assert "15 mm (read)" in found[0]["was_also_said"], \
        "the conflict does not say which size went unplaced"


def test_a_datum_that_fixes_two_directions_places_the_feature_in_neither():
    """
    The one this run turns on. 30 mm from the ring axis is 30 mm along the
    length or 30 mm up the height and the magnitude does not say which: an axis
    runs front to rear, so every distance from it lies in the plane of the
    length and the height. A datum fixing two directions is why the arm looked
    placed and was not, and naming the lower tangent plane — which fixes one —
    is what places it.
    """
    arm = dict(THE_FOOT_NOBODY_PLACED["features"][0])
    arm["position"] = dict(arm["position"],
                           **{"the lower tangent plane of the ring": _read("0 mm")})
    grown = merge({}, dict(THE_FOOT_NOBODY_PLACED, features=[arm]))

    assert not [conflict for conflict in grown.get("conflicts") or ()
                if "left-arm-and-fork-profile" in conflict["about"]]


def test_a_feature_with_no_size_in_a_direction_is_asked_for_no_position_in_it():
    """
    A Ø42 bore is a diameter and a radius is a radius: neither is a size along a
    named direction, and demanding a position for one would raise a finding on
    every hole on every part.
    """
    grown = merge({}, {
        "datums": [{"name": "the ring axis", "is": "the axis of the Ø42 bore",
                    "fixes": "the length and the height"}],
        "features": [{"id": "ring-bore", "kind": "Ø42 bore through the ring",
                      "axis": _read("along the ring axis, front to rear"),
                      "diameter": _read("42 mm")}],
    })

    assert "conflicts" not in grown


A_BLIND_POCKET_DESCRIBED_WHOLE = {
    "datums": [
        {"name": "the left end face", "is": "the flat face at the left", "fixes": "the length"},
        {"name": "the top face", "is": "the flat face at the top", "fixes": "the height"},
    ],
    "envelope": {"length": _read("80 mm"), "height": _read("20 mm")},
    "features": [
        {"id": "pocket-1", "kind": "Ø20 pocket", "topology": "blind",
         "axis": _read("perpendicular to the top face"),
         "depth": _read("6 mm"),
         "diameter": _read("20 mm"),
         "position": {"the left end face": _read("20 mm"), "the top face": _read("0 mm")},
         "seen_in": ["top view", "SECTION A-A"]},
    ],
}


def test_a_size_running_along_the_features_own_axis_raises_nothing():
    """
    A blind pocket 6 mm deep is 6 mm along its own axis, not 6 mm of the part's
    depth, and the position that placed the axis — 20 mm from the left end face,
    starting at the top face — has already placed it. Nothing about this pocket
    is missing, and a check that fires on work that is right is a check nobody
    keeps: the same argument that leaves fillets out of the plane words. Read
    the other way, this would raise a finding on every blind hole, pocket and
    counterbore on every part there is.
    """
    assert "conflicts" not in merge({}, A_BLIND_POCKET_DESCRIBED_WHOLE)


A_THROUGH_HOLE_PLACED_ALONG_ITS_OWN_AXIS = {
    "datums": [
        {"name": "the left end face", "is": "the flat face at the left", "fixes": "the length"},
        {"name": "the top face", "is": "the flat face at the top", "fixes": "the height"},
        {"name": "the front face", "is": "one of the two outer faces",
         "fixes": "the depth front to rear"},
        {"name": "the longitudinal centre plane", "is": "the plane through the centreline",
         "fixes": "the depth front to rear"},
    ],
    "envelope": {"length": _read("80 mm"), "height": _read("20 mm"),
                 "depth front to rear": _read("30 mm")},
    "features": [
        {"id": "hole-1", "kind": "Ø6.6 mounting hole", "topology": "through all",
         "axis": _read("perpendicular to the front face"),
         "position": {"the left end face": _read("14 mm"),
                      "the top face": _read("7 mm"),
                      "the longitudinal centre plane": _read("coincident")},
         "seen_in": ["top view", "bottom view"]},
    ],
}


def test_a_through_feature_placed_along_its_own_axis_is_a_conflict():
    """
    FCC-106, the half of it a present `axis` cannot answer. Nothing is
    positioned along its own axis, and a feature that goes through starts at no
    face — so an axis in the depth and a position keyed by a datum that fixes
    the depth cannot both be right.

    The recorded run (`logs/sessions/141a41df15544013a9740a45138fa7ad.jsonl`,
    the 2023 pipe clamp) wrote both M6 holes `perpendicular to the front face`
    while placing them on the centre plane of that same depth. The holes are
    drawn round in the plan and drilled through the height; built along the
    depth, the right-hand screw came out perpendicular to the split it exists
    to pinch. Nothing could contradict it: an `axis` that is merely present
    satisfies `direction`, and a wrong direction reads like a right one.
    """
    conflicts = merge({}, A_THROUGH_HOLE_PLACED_ALONG_ITS_OWN_AXIS)["conflicts"]

    assert [entry["about"] for entry in conflicts] == \
        ["features → hole-1 → position → the longitudinal centre plane"]
    assert "goes through" in conflicts[0]["stands"]
    assert "perpendicular to the front face" in conflicts[0]["was_also_said"]


def test_a_feature_placed_twice_over_along_its_own_axis_is_a_conflict():
    """
    Even a blind feature starts at one face only. The same run placed the
    central cutout 6 mm from the front face *and* 6 mm from the rear face, both
    along the axis it said the cutout runs on — two numbers taken off the `(6)`
    and `(6)` of SECTION A-A, which are the depths of the counterbore and the
    hex recess and belong to the mounting hole.
    """
    described = {
        "datums": [
            {"name": "the front face", "is": "one of the two outer faces",
             "fixes": "the depth front to rear"},
            {"name": "the rear face", "is": "the face opposite the front face",
             "fixes": "the depth front to rear"},
        ],
        "features": [
            {"id": "cutout-1", "kind": "central cutout", "topology": "open at the bottom",
             "axis": _read("parallel to the depth front to rear"),
             "position": {"the front face": _read("6 mm"), "the rear face": _read("6 mm")},
             "seen_in": ["VIEW B"]},
        ],
    }

    conflicts = merge({}, described)["conflicts"]

    assert [entry["about"] for entry in conflicts] == \
        ["features → cutout-1 → position → the front face",
         "features → cutout-1 → position → the rear face"]
    assert "can only start at one face" in conflicts[0]["stands"]


def test_an_axis_that_names_no_single_direction_places_nothing():
    """
    A datum that fixes two directions attributes neither — the ring axis runs
    front to rear, so a feature measured from it is measured along the length
    or up the height and the magnitude does not say which. An axis named only
    against one of those is an axis this cannot reason about, and a check that
    guesses is worse than no check.
    """
    described = {
        "datums": [{"name": "the ring axis", "is": "the axis through the ring",
                    "fixes": "the length and the height"}],
        "features": [
            {"id": "split-1", "kind": "split slot", "topology": "through",
             "axis": _read("perpendicular to the ring axis"),
             "position": {"the ring axis": _read("2 mm below")},
             "seen_in": ["front view"]},
        ],
    }

    assert "conflicts" not in merge({}, described)


def test_the_pocket_that_never_said_which_way_is_short_of_its_axis_and_nothing_else():
    """
    The same pocket with its axis taken off. A pocket's depth runs along its own
    axis whether or not anybody wrote that axis down, so the depth is still not
    a size in one of the part's directions and the one thing missing is the axis.
    Two findings for one gap is the check saying the same thing twice, and the
    second of them names something that was never wrong.
    """
    pocket = {key: value for key, value in A_BLIND_POCKET_DESCRIBED_WHOLE["features"][0].items()
              if key != "axis"}
    grown = merge({}, dict(A_BLIND_POCKET_DESCRIBED_WHOLE, features=[pocket]))

    assert [conflict["about"] for conflict in grown["conflicts"]] == \
        ["features → pocket-1 → axis"]


THE_PART = {
    "datums": [{"name": "the left end face", "is": "the flat face at the left"}],
    "envelope": {"length": _read("150 mm"), "depth front to rear": _read("100 mm")},
    "features": [{"id": "hole-40-left", "kind": "Ø40 hole", "topology": "through",
                  "position": {"the left end face": _read("20 mm")},
                  "seen_in": ["front view", "SECTION B-B"]}],
    "conflicts": [{"about": "features → hole-40-left → position → the left end face",
                   "stands": _read("20 mm"), "was_also_said": _read("25 mm")}],
    "not_defined": [{"what": "the general tolerance", "looked_in": ["the notes"]}],
}


def test_the_part_reads_as_a_structure_and_not_as_json():
    written = render(THE_PART)

    assert "{" not in written and '"' not in written, "the Gate was shown a JSON dump"
    for heading in ("Datums", "Envelope", "Features", "In conflict", "Not defined"):
        assert heading in written, "the Gate lost the " + heading + " section"


def test_every_value_carries_its_datum_and_its_flag_on_the_line():
    """FCC-87's discipline, structural now rather than one line of prose per value."""
    written = render(THE_PART)

    assert "the left end face: 20 mm (read)" in written
    assert "length: 150 mm (read)" in written
    assert "seen in: front view, SECTION B-B" in written
    assert "- hole-40-left" in written
    assert "kind: Ø40 hole" in written and "topology: through" in written


def test_a_conflict_and_a_gap_are_shown_apart_from_the_part():
    written = render(THE_PART)
    conflicts = written.split("In conflict")[1]

    assert "stands: 20 mm (read)" in conflicts
    assert "was also said: 25 mm (read)" in conflicts
    assert "the general tolerance" in written.split("Not defined")[1]


THE_SAME_DISAGREEMENT_TWICE = {
    "features": [{"id": "the-arm", "kind": "the angled arm",
                  "profile": _read("40 mm wide")}],
    "conflicts": [{"about": "features \u2192 the-arm \u2192 profile, in VIEW C-C, on page 2",
                   "stands": "the sheet prints it",
                   "was_also_said": "the view draws it otherwise"},
                  {"about": "features \u2192 the-arm \u2192 profile, in VIEW C-C, page 2",
                   "stands": "the sheet prints 40 mm",
                   "was_also_said": "the view draws it otherwise"}],
}


def test_one_disagreement_spelled_two_ways_is_one_entry():
    """
    The reader names a view after where it sits and the description writes down
    what it read, so "VIEW C-C, on page 2" and "VIEW C-C, page 2" are one view
    and the address they are written under is one address. Compared as they
    stand they are two, and a re-read of the same sheet then manufactures a
    disagreement nobody found — which is the same rule `_as_a_view` keeps for a
    view a circle was traced round in, applied to what an entry is about.
    """
    reading = merge({}, THE_SAME_DISAGREEMENT_TWICE)
    standing = each_disagreement_once(reading)["conflicts"]

    assert len(reading["conflicts"]) == 2
    assert [conflict["about"] for conflict in standing] == \
        [THE_SAME_DISAGREEMENT_TWICE["conflicts"][0]["about"]]
    assert render(reading).count("the view draws it otherwise") == 1
    assert "conflicts" not in distilled(reading)


A_PART_NOBODY_AGREES_ON = dict(
    THE_PART,
    conflicts=THE_PART["conflicts"] + [
        {"about": "features \u2192 the-slot \u2192 axis",
         "stands": "nothing records which way its axis points",
         "kind": design.A_GAP},
        {"about": "the line work traced in the front view",
         "stands": "the solver could not be asked about it",
         "kind": design.A_CHECK_HAZARD}],
)


def test_the_gate_reads_the_conflicts_grouped_by_kind():
    """
    Three kinds under three headings, because a user settles the three
    differently: a reading that cannot stand beside another is theirs to
    choose between, a value nothing supplies is theirs to give, and a check
    nobody could run is neither — it is a sheet that was not read rather than
    one that was read and agrees. Read as one flat column the recorded run put
    four entries naming what ended it among twenty-nine that did not.
    """
    written = render(A_PART_NOBODY_AGREES_ON)
    conflicts = written.split("In conflict")[1]

    assert conflicts.index(design.HEADED[design.A_CONFLICT]) \
        < conflicts.index(design.HEADED[design.A_GAP]) \
        < conflicts.index(design.HEADED[design.A_CHECK_HAZARD])
    assert "features \u2192 the-slot \u2192 axis" in \
        conflicts.split(design.HEADED[design.A_GAP])[1] \
        .split(design.HEADED[design.A_CHECK_HAZARD])[0]
    assert "kind:" not in conflicts, "the heading already says which kind it is"


A_CAVITY_NOTHING_PLACES_OR_POINTS = {
    "features": [{"id": "the-slot", "kind": "the arm slot", "topology": "through",
                  "height": _read("15 mm"), "seen_in": ["VIEW B"]}],
}


def test_a_value_nothing_on_the_part_supplies_is_a_gap_and_not_a_disagreement():
    """
    What `direction`, `placement` and `twice_over` find is nobody's second
    reading: the slot has a size in the height and nothing placing it there, no
    axis recorded, and no position at all. There is nothing for a user to
    choose between, so it is a value they have to give — which is what a gap
    is, and it is not what the Gate offers a choice about.
    """
    reading = each_disagreement_once(merge({}, A_CAVITY_NOTHING_PLACES_OR_POINTS))

    assert {conflict["kind"] for conflict in reading["conflicts"]} == {design.A_GAP}
    assert len(reading["conflicts"]) == 3


def test_a_part_nobody_has_described_renders_as_nothing():
    assert render({}) == ""
    assert render(None) == ""


def _about(model):
    return [entry["about"] for entry in entries(model)]


def test_every_value_is_addressed_by_where_it_sits():
    """
    The address is the path the merge already records a conflict under, so an
    entry is cited by exactly the words it is kept under and there is one
    notation, not two. Matched by identity in a list, never by position: a
    feature added ahead of another must not move the address of everything
    after it. FCC-94.
    """
    assert _about(THE_PART) == [
        "envelope → length",
        "envelope → depth front to rear",
        "features → hole-40-left → position → the left end face",
    ]


def test_an_envelope_entry_is_as_addressable_as_a_feature():
    """
    The value the recorded run lost between the description and the Plan was
    the 100 mm front to rear — an envelope entry, not a feature. An enumeration
    that only reached features could not have caught it. FCC-94.
    """
    assert "envelope → depth front to rear" in _about(THE_PART)


def test_an_entry_carries_its_datum_and_its_flag_into_the_words():
    """
    A criterion is the description's own entry and not a paraphrase of it: the
    datum is the key it sits under, and it reaches the assert message with the
    flag still on it. FCC-94.
    """
    said = [entry["says"] for entry in entries(THE_PART)]

    assert "features → hole-40-left → position → the left end face: 20 mm (read)" in said
    assert "envelope → depth front to rear: 100 mm (read)" in said
    assert [entry["flag"] for entry in entries(THE_PART)] == ["read", "read", "read"]


def test_an_assumed_value_is_an_entry_and_says_so():
    """
    Dropping it would hide from the checks the one kind of value most likely to
    be wrong, and the user has to see at the Gate that it is theirs. FCC-94.
    """
    entry = entries({"envelope": {"length": {"value": "150 mm", "flag": "assumed"}}})[0]

    assert entry["flag"] == "assumed"
    assert entry["says"] == "envelope → length: 150 mm (assumed)"


def test_a_conflict_and_a_gap_are_not_criteria():
    """
    A conflict holds two values of which neither is settled, so a criterion
    derived from one would assert whichever half the merge happened to keep —
    the machine settling what ADR-0019 says the user settles. FCC-94.
    """
    assert not [entry for entry in entries(THE_PART) if "conflict" in entry["about"]]
    assert not [entry for entry in entries(THE_PART) if "not_defined" in entry["about"]]


def test_a_part_nobody_has_described_carries_no_entries():
    """No description means an empty Acceptance, which means no Assertion phase."""
    assert entries({}) == []
    assert entries(None) == []


THE_ARM_REPLACED = {
    "envelope": {"length": _read("108 mm")},
    "features": [
        {"id": "left-mounting-ear", "kind": "horizontal left mounting ear",
         "superseded_by": "phase2-angled-left-arm",
         "position": {"the left end tangent plane": _read("0 mm")}},
        {"id": "phase2-angled-left-arm", "kind": "left arm at 45°",
         "position": {"the ring axis": _read("73 mm along the arm")}},
    ],
}


def test_a_feature_the_later_page_replaces_is_no_longer_the_part():
    """
    One part, not one model of two parts. The Model Mania run kept the Phase 1
    ear beside the Phase 2 arm, both `read`: every entry of the superseded ear
    was a criterion the part had to meet and a Step Plan had to write, so the
    Plan built a mounting bore the second page had deleted and spent a revision
    being told it had left one unplanned. FCC-127.
    """
    about = [entry["about"] for entry in entries(THE_ARM_REPLACED)]

    assert about == ["envelope → length",
                     "features → phase2-angled-left-arm → position → the ring axis"]


def test_a_feature_that_is_gone_is_not_checked_for_what_it_never_said():
    """
    Not the part any more means not judged either: a finding about a feature the
    later page deleted is a conflict at the Gate about geometry nobody is going
    to build. FCC-127.
    """
    silent = merge({}, {"features": [
        {"id": "left-bore", "kind": "Ø6.6 bore", "topology": "through",
         "superseded_by": "phase2-slot", "position": {}},
        {"id": "phase2-slot", "kind": "Ø6.6 obround slot", "topology": "through",
         "axis": _read("perpendicular to the top face"),
         "position": {"the left end face": _read("15 mm")}},
    ]})

    assert not [found for found in silent.get("conflicts") or ()
                if "left-bore" in str(found.get("about"))]


def test_the_replacement_is_still_read_at_the_gate():
    """
    Out of the criteria, not out of the description: what the later page
    replaced is what the user checks the reading against.
    """
    written = render(THE_ARM_REPLACED)

    assert "- left-mounting-ear" in written
    assert "superseded by: phase2-angled-left-arm" in written


THE_CAVITY_DESCRIBED_TWICE = {
    "envelope": {"length between the end faces": _read("108 mm")},
    "features": [
        {"id": "slot-arm",
         "kind": "slot through the 15 mm arm, 30 mm long in the length direction",
         "topology": "through the arm thickness",
         "position": {"the ring axis": _read("30 mm to the left of the ring axis")},
         "seen_in": ["plan view", "front view"]},
        {"id": "cutout-web",
         "kind": "curved cutout in the web between the top of the arm and the ring",
         "topology": "through the depth; profile as drawn in VIEW B",
         "position": {},
         "seen_in": ["front view", "VIEW B"]},
    ],
}


def test_a_cavity_nothing_places_is_a_conflict():
    """
    The recorded run
    (`logs/sessions/e76b61d5bc7143699b451549c90433f7.jsonl`, the 2023 pipe
    clamp) described the web's one cavity twice — once off the plan as a 30 mm
    slot and once off the detail the sheet points at — and the second carried
    `position: {}`. Plan built both, inventing a width for one and an R10 circle
    for the other, and the circle cut the Body in two.

    `placement` cannot reach this: it asks whether a size in a direction has a
    position in that direction, and this feature had no sizes either.
    """
    grown = merge({}, THE_CAVITY_DESCRIBED_TWICE)
    said = [conflict for conflict in grown["conflicts"]
            if conflict["about"] == "features → cutout-web → position"]

    assert said, [conflict["about"] for conflict in grown["conflicts"]]
    assert "slot-arm" in said[0]["stands"], \
        "the finding does not say which other cavity shares the view: %s" % said[0]["stands"]


def test_a_cavity_the_drawing_places_raises_nothing():
    """Silence on the ordinary pocket, or the finding is noise at every Gate."""
    grown = merge({}, {"features": [
        {"id": "pocket", "kind": "rectangular pocket", "topology": "6 mm deep",
         "axis": _read("perpendicular to the top face"),
         "position": {"the left end face": _read("20 mm")},
         "seen_in": ["top view"]}]})

    assert "conflicts" not in grown


def test_the_outside_being_nobody_s_is_answerable_rather_than_only_recorded():
    """
    The finding stood in `conflicts` for the whole of the recorded run and
    nothing acted on it. The loop asks the drawing and then the user before it
    plans, so it needs the finding as a question rather than as a list entry —
    and it needs it to stop being true the moment a feature carries the contour.
    """
    from core.design import nobody_carries_the_outside

    said_in_passing = merge({}, THE_CAPSULE_SAID_IN_PASSING)
    assert "capsule" in nobody_carries_the_outside(said_in_passing)

    carried = merge(said_in_passing, {"features": [
        {"id": "outer-profile",
         "kind": "obround (capsule) outer profile, 150 x 50, ends R25",
         "plane": _read("the plane of the length and the height")}]})
    assert nobody_carries_the_outside(carried) == ""


def test_the_finding_quotes_the_description_rather_than_the_sheet():
    """
    `printed_on_the_sheet` is the draughtsman's own callouts, and quoted back as
    evidence the finding reads as `R12.5 — plan view (rounded ends)` — the
    sheet, not the reading the user is being asked to settle. The description's
    own words are preferred; the sweep is what is left when it has none.
    """
    from core.design import nobody_carries_the_outside

    both = merge({}, dict(THE_CAPSULE_SAID_IN_PASSING,
                          printed_on_the_sheet=["R12.5 — plan view (rounded ends)"]))

    assert "R12.5" not in nobody_carries_the_outside(both)
    assert "capsule" in nobody_carries_the_outside(both)


def _round_in(view, axis):
    return {"value": axis, "flag": "traced", "round_in": view}


THE_CLAMP_READ_OFF_ONE_VIEW = {
    "features": [
        {"id": "clamp-bore", "kind": "Ø42 bore through the clamp ring",
         "topology": "through", "axis": _round_in("the front elevation", "the depth")},
        {"id": "right-mounting-hole", "kind": "Ø6.6 through hole",
         "topology": "through", "axis": _round_in("the upper plan", "the depth")},
    ],
}


def test_two_features_round_in_different_views_may_not_share_an_axis():
    """
    The 2023 pipe clamp. The bore is round in the elevation and both M6 holes
    are round in the plan; all three were written along the depth, all three
    were built parallel, and the screw missed the split it exists to pinch.
    """
    found = crosswise(THE_CLAMP_READ_OFF_ONE_VIEW)

    assert [entry["about"] for entry in found] == [
        "features → clamp-bore → axis", "features → right-mounting-hole → axis"]
    assert "two views" in found[0]["stands"]


def test_the_finding_names_the_other_view_so_the_reader_can_go_and_look():
    found = crosswise(THE_CLAMP_READ_OFF_ONE_VIEW)

    assert "the upper plan" in found[0]["stands"]
    assert "the front elevation" in found[1]["stands"]


def test_features_round_in_different_views_with_different_axes_are_right():
    read_properly = {"features": [
        {"id": "clamp-bore", "kind": "Ø42 bore", "topology": "through",
         "axis": _round_in("the front elevation", "the depth")},
        {"id": "right-mounting-hole", "kind": "Ø6.6 through hole",
         "topology": "through", "axis": _round_in("the upper plan", "the height")},
    ]}

    assert crosswise(read_properly) == []


def test_two_features_round_in_one_view_may_not_have_two_axes():
    disagreeing = {"features": [
        {"id": "left-mounting-hole", "kind": "Ø6.6 hole", "topology": "through",
         "axis": _round_in("the upper plan", "the height")},
        {"id": "right-mounting-hole", "kind": "Ø6.6 hole", "topology": "through",
         "axis": _round_in("the upper plan", "the depth")},
    ]}

    found = crosswise(disagreeing)

    assert len(found) == 2
    assert "round in the upper plan has the one axis" in found[0]["stands"]


def test_a_reading_that_never_says_which_view_raises_nothing_here():
    """`direction` is what asks for the field; this only reads it."""
    assert crosswise({"features": [
        {"id": "clamp-bore", "kind": "Ø42 bore", "topology": "through",
         "axis": {"value": "the depth", "flag": "read"}},
        {"id": "right-mounting-hole", "kind": "Ø6.6 hole", "topology": "through",
         "axis": {"value": "the depth", "flag": "read"}},
    ]}) == []


def test_the_finding_arrives_through_the_merge_like_every_other():
    grown = merge({}, THE_CLAMP_READ_OFF_ONE_VIEW)

    assert any("two views" in str(entry.get("stands")) for entry in grown["conflicts"])


THE_SPLIT_AND_THE_CUTOUT = {
    "features": [
        {"id": "clamp-ring-bore", "kind": "\u00d842 circular clamp bore",
         "diameter": {"value": "42 mm", "flag": "read"},
         "axis": _round_in("the middle centre of the sheet, on page 1", "the depth")},
        {"id": "ring-split", "kind": "right-side split through the clamp ring",
         "topology": "open split continuing from the \u00d842 clamp bore to the right end face",
         "axis": _round_in("the middle centre of the sheet, on page 1", "the length")},
        {"id": "web-cutout", "kind": "through cutout between the lower arm and the ring",
         "axis": _round_in("VIEW B, page 1", "the depth")},
    ],
}


def test_a_saw_cut_and_a_cutout_are_round_in_nothing():
    """
    The run that stopped before the Plan Gate
    (`logs/sessions/daa17fb149474317840ef98439cfef6f.jsonl`). The prompt asks
    every feature for `round_in`, so a split and an open cutout got one, and
    `crosswise` then judged a saw cut against a bore — four findings, none of
    them about anything that has a circle.
    """
    assert crosswise(THE_SPLIT_AND_THE_CUTOUT) == []


def test_a_bore_named_in_the_topology_of_something_else_does_not_make_it_round():
    """`ring-split` says it continues from the Ø42 bore. That is the bore's."""
    assert not design._is_round(THE_SPLIT_AND_THE_CUTOUT["features"][1])


def test_two_holes_are_still_judged_against_each_other():
    """Narrowed to what is round, not switched off."""
    assert len(crosswise(THE_CLAMP_READ_OFF_ONE_VIEW)) == 2


A_TRACE = {
    "the middle centre of the sheet, on page 1": [41.64, 24.74],
    "VIEW C-C, on page 2": [5.68, 19.52],
}


def _a_bore(view, wide="42 mm"):
    return {"features": [
        {"id": "clamp-ring-bore", "kind": "\u00d842 clamp bore", "topology": "through",
         "diameter": {"value": wide, "flag": "read"},
         "axis": _round_in(view, "the depth")}]}


def test_a_circle_traced_in_another_view_names_that_view():
    found = grounded(_a_bore("VIEW C-C, on page 2"), A_TRACE)

    assert [entry["about"] for entry in found] == ["features → clamp-ring-bore → axis"]
    assert "the middle centre of the sheet, on page 1" in found[0]["stands"]
    assert found[0]["instead"] == "the middle centre of the sheet, on page 1"


def test_a_reading_the_trace_agrees_with_raises_nothing():
    assert grounded(_a_bore("the middle centre of the sheet, on page 1"), A_TRACE) == []


def test_the_view_is_matched_however_the_two_of_them_say_it():
    """The reader writes "VIEW C-C, on page 2" and the description "VIEW C-C, page 2"."""
    assert grounded({"features": [
        {"id": "left-mounting-slot", "kind": "\u00d86.6 obround mounting slot",
         "diameter": {"value": "6.6 mm", "flag": "read"},
         "axis": _round_in("VIEW C-C, page 2", "the depth")}]}, A_TRACE) == []


def test_a_circle_traced_nowhere_at_all_is_no_evidence_against_the_reading():
    """
    The tracer misses circles constantly. Absence of a trace is not absence of
    a bore, and a check that read it that way would fire on a correct reading.
    """
    assert grounded(_a_bore("VIEW C-C, on page 2", wide="88 mm"), A_TRACE) == []


def test_with_no_sheet_to_hold_it_against_the_check_says_nothing():
    assert grounded(_a_bore("VIEW C-C, on page 2"), {}) == []


def test_the_drawing_settles_which_of_two_disagreeing_features_is_wrong():
    """
    The stopped run's own case: the bore is confirmed Ø42 in the view it names,
    the other is not, and `crosswise` cannot tell which of the two is misread.
    Holding the confirmed one to account is what stopped a correct reading.
    """
    two = {"features": _a_bore("the middle centre of the sheet, on page 1")["features"]
           + [{"id": "a-hole", "kind": "\u00d86.6 through hole",
               "diameter": {"value": "6.6 mm", "flag": "read"},
               "axis": _round_in("the upper centre of the sheet, on page 1", "the depth")}]}

    assert len(crosswise(two)) == 2

    about = [entry["about"] for entry in read_off_the_wrong_view(two, A_TRACE)]
    assert "features → clamp-ring-bore → axis" not in about
    assert "features → a-hole → axis" in about


def test_where_the_drawing_confirms_them_all_the_disagreement_is_the_drawings():
    both = {"features": _a_bore("the middle centre of the sheet, on page 1")["features"]
            + [{"id": "a-slot", "kind": "\u00d86.6 obround slot",
                "diameter": {"value": "6.6 mm", "flag": "read"},
                "axis": _round_in("VIEW C-C, on page 2", "the depth")}]}

    assert len(read_off_the_wrong_view(both, A_TRACE)) == 2


def test_a_correction_lands_at_the_address_and_keeps_what_it_displaced():
    model = _a_bore("VIEW C-C, on page 2")

    corrected(model, "features → clamp-ring-bore → axis", "the height",
              "\u00d842 was traced in the middle centre view")

    axis = model["features"][0]["axis"]
    assert axis["value"] == "the height"
    assert axis["was"] == "the depth"
    assert "traced" in axis["because"]


def test_a_correction_may_carry_the_whole_record_including_the_view():
    model = _a_bore("VIEW C-C, on page 2")

    corrected(model, "features → clamp-ring-bore → axis",
              _round_in("the middle centre of the sheet, on page 1", "the height"),
              "the trace")

    assert design.said_at(model, "features → clamp-ring-bore → axis") == "the height"
    assert grounded(model, A_TRACE) == []


def test_an_address_nobody_filled_is_not_a_correction():
    """This overrules; it does not create. An empty field is the merge's."""
    model = _a_bore("VIEW C-C, on page 2")

    corrected(model, "features → clamp-ring-bore → plane", "the plan", "the trace")
    corrected(model, "features → nothing-of-the-sort → axis", "the plan", "the trace")

    assert "plane" not in model["features"][0]
    assert len(model["features"]) == 1


def test_the_merge_still_refuses_everything_that_is_not_a_correction():
    """The rule the phase rests on is untouched: this is the one exception."""
    second = _a_bore("VIEW C-C, on page 2")
    second["features"][0]["axis"] = _round_in("VIEW C-C, on page 2", "the height")

    grown = merge(_a_bore("VIEW C-C, on page 2"), second)

    assert design.said_at(grown, "features → clamp-ring-bore → axis") == "the depth"
    assert any("the height" in str(entry) for entry in grown["conflicts"])


def test_a_finding_about_a_position_is_corrected_where_the_direction_is():
    """
    `pointing` addresses the position it could not reconcile with the axis —
    `features → X → position → <datum>` — because it cannot tell which of the
    two is wrong. The re-read is asked for a direction, so the answer is one,
    and a direction goes where directions go.
    """
    model = _a_bore("VIEW C-C, on page 2")

    assert where_the_direction_is(
        model, "features → clamp-ring-bore → position → the ring axis") \
        == "features → clamp-ring-bore → axis"


def test_a_finding_already_about_the_direction_is_left_where_it_is():
    """`grounded` and `crosswise` address the field the direction is in already."""
    at = "features → clamp-ring-bore → axis"

    assert where_the_direction_is(_a_bore("VIEW C-C, on page 2"), at) == at


def test_the_direction_is_the_plane_where_that_is_what_carries_the_view():
    model = {"features": [{"id": "front-central-pocket", "kind": "round pocket",
                           "plane": _round_in("VIEW C-C, on page 2", "the depth")}]}

    assert where_the_direction_is(
        model, "features → front-central-pocket → position → the top face") \
        == "features → front-central-pocket → plane"


def test_a_diameter_that_is_not_a_measurement_is_no_width():
    """
    "0.47 of the overall length" is a proportion and "M6" is a thread callout.
    The first number out of either is a millimetre width nobody measured, and
    it is matched at ±1.5 mm against the traced arcs — so the trace confirms a
    reading, or contradicts it, at random.
    """
    for said, traced in (("0.47 of the overall length", 1.0), ("M6", 7.0)):
        model = {"features": [{"id": "a-hole", "kind": "tapped hole",
                               "topology": "through",
                               "diameter": {"value": said, "flag": "read"},
                               "axis": _round_in("VIEW C-C, on page 2", "the depth")}]}

        assert grounded(model, {"the middle centre of the sheet, on page 1":
                                [traced]}) == [], said


def test_a_diameter_that_is_a_measurement_is_still_a_width():
    """Narrowed to what was measured, not switched off."""
    assert len(grounded(_a_bore("VIEW C-C, on page 2", wide="Ø42"), A_TRACE)) == 1


def test_a_value_the_model_settled_is_read_off_the_model_with_its_reason():
    """
    Nothing accumulates a list of decisions beside the description — that is
    `you_asked_and_were_answered` again, and downstream would prefer the newer
    of the two. The section the Gate shows is walked off the part itself.
    """
    from core.design import decided_for_you

    model = {"envelope": {"length": {"value": "150 mm", "flag": "read"}},
             "features": [{"id": "outer-profile",
                           "plane": {"value": "the plane of the elevation",
                                     "flag": "assumed",
                                     "because": "nobody carries the outer contour, so "
                                                "it was read off the silhouette"}}]}

    said = decided_for_you(model)

    assert len(said) == 1
    assert "features → outer-profile → plane" in said[0]
    assert "the plane of the elevation" in said[0]
    assert "read off the silhouette" in said[0]


def test_a_correction_says_what_it_was_decided_against():
    """A displaced value is the other half of the line: this, and not that."""
    from core.design import decided_for_you

    model = _a_bore("VIEW C-C, on page 2")
    corrected(model, "features → clamp-ring-bore → axis", "the height",
              "something Ø42 was traced in the middle centre view")

    said = decided_for_you(model)

    assert len(said) == 1
    assert "the height" in said[0]
    assert "the depth" in said[0], "the direction it displaced is not on the line"
    assert "traced in the middle centre view" in said[0]


def test_a_value_the_drawing_gave_is_nobodys_decision():
    """`read` and `derived` are the sheet's own; only what nobody supplied is here."""
    from core.design import decided_for_you

    assert decided_for_you(DEPTH_OFF_THE_SECTION) == []


AN_ASSUMED_OVERALL_NOBODY_CLAIMED = {
    "envelope": {"height": {"value": "50 mm", "flag": "assumed"},
                 "length": {"value": "150 mm", "flag": "read"}},
}


def test_an_assumed_value_with_no_because_is_a_conflict():
    """
    The requirement was written in `config/prompts/design.md` and nowhere else,
    so the one thing that tells a user the model settled a value rested on the
    model doing as it was told. It is a reading defect like every other: the run
    carries on and the Plan Gate shows it.
    """
    grown = merge({}, AN_ASSUMED_OVERALL_NOBODY_CLAIMED)
    said = [conflict for conflict in grown["conflicts"]
            if conflict["about"] == "envelope \u2192 height"]

    assert said, [conflict["about"] for conflict in grown["conflicts"]]
    assert "because" in said[0]["stands"]


def test_an_empty_because_is_no_because():
    """A field written and left blank says as little as one never written."""
    grown = merge({}, {"envelope": {"height": {"value": "50 mm", "flag": "assumed",
                                              "because": ""}}})

    assert [conflict for conflict in grown["conflicts"]
            if conflict["about"] == "envelope \u2192 height"]


def test_it_is_the_finding_derived_with_no_from_gets():
    """
    One rule about incomplete entries rather than two: an entry that does not
    say what settled it is the same defect whichever flag it carries, so it is
    raised at the same site and in the same words.
    """
    grown = merge({}, {"envelope": {"height": {"value": "50 mm", "flag": "assumed"},
                                    "width": {"value": "40 mm", "flag": "derived"}}})
    at = {conflict["about"]: conflict for conflict in grown["conflicts"]}

    assumed, derived = at["envelope \u2192 height"], at["envelope \u2192 width"]

    assert assumed["stands"].replace("assumed", "derived").replace("because", "from") \
        == derived["stands"]


def test_a_well_formed_assumed_passes_untouched():
    """Silence on the value that says who settled it, or the Gate is noise."""
    grown = merge({}, {"envelope": {
        "height": {"value": "50 mm", "flag": "assumed",
                   "because": "nothing carries the outer contour; read off the "
                              "silhouette, half the 50 mm height"},
        "width": {"value": "40 mm", "flag": "derived",
                  "from": "the front view's line work: the arm's faces at "
                          "y=37.99 and 23.00"}}})

    assert "conflicts" not in grown


def test_a_value_that_passed_is_never_nobodys_at_the_gate():
    """
    `decided_for_you` fell back to prose saying nothing settled the value, which
    is the section AC5 of FCC-127 rests on. Nothing reaches that fallback once
    the check has let the model through.
    """
    from core.design import _NOBODY_SAID, decided_for_you

    grown = merge({}, {"envelope": {"height": {"value": "50 mm", "flag": "assumed",
                                              "because": "read off the silhouette"}}})

    assert "conflicts" not in grown
    assert decided_for_you(grown) == ["envelope \u2192 height: 50 mm \u2014 read off the silhouette"]
    assert not any(_NOBODY_SAID in said for said in decided_for_you(grown))


THE_TRACED_POCKET = (
    "the middle-left view's traced pocket profile has straight-edge endpoints "
    "at approximately x=89.45 mm and x=129.56 mm, with y=28.36 mm and "
    "y=53.23 mm; its concave side arcs are approximately R30 and centred at "
    "the end-bore axes. This shown profile is approximately 40 mm wide by "
    "25 mm high, and its arc intersections are consistent with the shown "
    "bore-axis centres.")

THE_STATED_POCKET = (
    "the existing front-central-pocket and rear-central-pocket entries state a "
    "40 mm wide by 30 mm high profile, with lower and upper edges 10 mm from "
    "the bottom and top tangent planes. The sheet does not dimension those "
    "edges 10 mm from the height boundaries: the two 10 mm callouts in "
    "SECTION B-B measure the depth-side walls beside the central opening, not "
    "the pocket height.")

THE_POCKET_IN_CONFLICT = {
    "features": [
        {"id": "front-central-pocket", "kind": "central concave-sided pocket",
         "axis": _read("perpendicular to the front face"),
         "profile": {"value": "40 mm wide by 30 mm high, bounded above and below "
                              "by straight edges and at each side by a concave "
                              "R30 arc",
                     "flag": "derived",
                     "from": "the middle-left view line work and SECTION A-A's "
                             "10 mm top and bottom wall dimensions"},
         "radius": _read("R30")},
        {"id": "rear-central-pocket", "kind": "central concave-sided pocket",
         "profile": {"value": "same 40 mm wide by 30 mm high concave-sided "
                              "profile as the front central pocket",
                     "flag": "derived",
                     "from": "SECTION A-A and SECTION B-B show matching pockets"}},
    ],
    "conflicts": [
        {"about": "the value 125 on this part",
         "stands": "no callout prints it, no dimension line measures it and "
                   "nothing traced is that size",
         "was_also_said": "the drawing"},
        {"about": "central-pocket profile dimensions and arc intersections",
         "stands": THE_STATED_POCKET,
         "was_also_said": THE_TRACED_POCKET},
    ],
}


def test_the_reading_that_came_off_the_picture_settles_the_conflict():
    """
    The run that named this defect (`logs/sessions/
    f8aaa6953cf54651bd57a3f65432d474.jsonl`, mm25-p1-1.pdf) filed this conflict
    with the tracing in it and planned neither reading: it took the stated 30 mm
    height, kept the R30 centres, and built a pocket 48.038476 mm wide. Only one
    of the two readings was measured off the view, and it is the profile the
    feature is settled at.
    """
    found = settled_by_the_tracing(THE_POCKET_IN_CONFLICT)

    assert [finding["about"] for finding in found] == [
        "features \u2192 front-central-pocket \u2192 profile",
        "features \u2192 rear-central-pocket \u2192 profile"]
    assert found[0]["instead"] == THE_TRACED_POCKET
    assert "central-pocket profile" in found[0]["stands"]


def test_the_field_settled_is_the_one_the_conflict_is_about():
    """
    A conflict about a profile says nothing about the axis or the radius beside
    it, and a tracing written over those would be the third profile again.
    """
    found = settled_by_the_tracing(THE_POCKET_IN_CONFLICT)

    assert not [finding for finding in found
                if finding["about"].rsplit(UNDER, 1)[-1] != "profile"]


def test_a_conflict_neither_of_whose_readings_was_traced_is_nobodys():
    """
    Every other conflict rides to the Gate in the words of both: the tracing is
    what earns the overrule, and prose about a tracing is not one.
    """
    assert settled_by_the_tracing({
        "features": [{"id": "front-central-pocket",
                      "profile": _read("40 mm wide by 30 mm high")}],
        "conflicts": [{"about": "central-pocket profile",
                       "stands": "the front view prints 30 mm",
                       "was_also_said": "SECTION A-A prints 25 mm"}]}) == []


def test_a_tracing_that_names_no_feature_settles_nothing():
    """An address nothing names is an address nothing can be written at."""
    assert settled_by_the_tracing({
        "features": [{"id": "the-arm", "profile": _read("40 mm wide")}],
        "conflicts": [{"about": "central-pocket profile",
                       "stands": THE_STATED_POCKET,
                       "was_also_said": THE_TRACED_POCKET}]}) == []


def test_the_tracing_written_in_is_the_models_own_choice_at_the_gate():
    """
    The value the Plan plans with, and the disagreement the user sees: the
    traced profile is `assumed` with the conflict as its `because`, the 30 mm
    reading it displaced stands beside it under `was`, and the conflict itself
    is untouched on the part.
    """
    from core.design import decided_for_you

    model = json.loads(json.dumps(THE_POCKET_IN_CONFLICT))
    at = "features \u2192 front-central-pocket \u2192 profile"
    for finding in settled_by_the_tracing(model):
        corrected(model, finding["about"], {"value": finding["instead"],
                                            "flag": "assumed"}, finding["stands"])

    assert said_at(model, at) == THE_TRACED_POCKET
    assert entry_at(model, at)["flag"] == "assumed"
    assert "40 mm wide by 30 mm high" in entry_at(model, at)["was"]
    assert len(model["conflicts"]) == 2
    assert [said for said in decided_for_you(model) if said.startswith(at)]


def test_a_tracing_already_written_in_is_not_written_again():
    """
    The description is re-read and re-merged as the run goes on, and a finding
    that fires twice would displace the tracing with itself and lose the
    reading it stands against.
    """
    model = json.loads(json.dumps(THE_POCKET_IN_CONFLICT))
    for finding in settled_by_the_tracing(model):
        corrected(model, finding["about"], {"value": finding["instead"],
                                            "flag": "assumed"}, finding["stands"])

    assert settled_by_the_tracing(model) == []


THE_BORE_AND_ITS_EVIDENCE = {
    "printed_on_the_sheet": [{"callout": "\u00d842", "view": "the front elevation"}],
    "datums": [{"name": "the left end face", "is": "the flat face at the left"}],
    "envelope": {"length": _read("108 mm")},
    "features": [{"id": "ring-bore", "kind": "\u00d842 bore", "topology": "through",
                  "axis": {"value": "the depth", "flag": "derived",
                           "from": "the front elevation's line work",
                           "round_in": "the front elevation"},
                  "position": {"the left end face": {"value": "65.5 mm",
                                                     "flag": "assumed",
                                                     "because": "half the length"}},
                  "seen_in": ["the front elevation"]}],
    "conflicts": [{"about": "envelope \u2192 length", "stands": "108 mm",
                   "was_also_said": "180 mm"}],
    "not_defined": [{"what": "the material", "looked_in": ["the title block"]}],
}


def test_the_document_is_the_part_without_the_evidence_that_settled_it():
    """
    The second of Design's two steps. What goes is what proves a value rather
    than states it — the sweep of the sheet, the disagreements, what fixed a
    derived value and the views it was read in — and what stays is the part,
    entry for entry: the same addresses `entries` has always walked, so the
    Acceptance is the same list either way. What nobody has supplied stays
    beside it and is none of those addresses.
    """
    document = distilled(THE_BORE_AND_ITS_EVIDENCE)
    bore = document["features"][0]

    assert list(document) == ["datums", "envelope", "features", "not_defined"]
    assert bore["axis"] == {"value": "the depth", "flag": "derived"}
    assert "seen_in" not in bore
    assert [entry["about"] for entry in entries(document)] == \
        [entry["about"] for entry in entries(THE_BORE_AND_ITS_EVIDENCE)]


def test_a_value_the_model_settled_says_so_in_the_document():
    """
    `because` is not the proof of a number off the sheet: it is what the model
    settled a value the sheet never gave, which the Gate shows through
    `decided_for_you` and the user overrules in the file.
    """
    document = distilled(THE_BORE_AND_ITS_EVIDENCE)

    assert decided_for_you(document) == decided_for_you(THE_BORE_AND_ITS_EVIDENCE)
    assert decided_for_you(document)[0].endswith("half the length")


THE_SPLIT_THE_DRAWING_ANSWERED = {
    "features": [{"id": "ring-split", "kind": "radial split through the ring",
                  "topology": "open slot",
                  "width": {"value": "3 mm between the split faces", "flag": "derived",
                            "from": "the 1 mm and the 2 mm callout either side of "
                                    "the ring axis"},
                  "seen_in": ["SECTION A-A"]}],
    "not_defined": [{"what": "the width of the ring split",
                     "looked_in": ["SECTION A-A"]},
                    {"what": "general dimensional tolerances",
                     "looked_in": ["the notes"]}],
}


def test_a_gap_the_model_now_carries_the_value_for_is_retired():
    """
    What nobody supplies is what nobody supplies now. The split was measured off
    the two callouts either side of it after the gap was written down, and the
    gap went on saying nobody had given a width while the width stood beside it.
    """
    document = distilled(THE_SPLIT_THE_DRAWING_ANSWERED)

    assert [gap["what"] for gap in document["not_defined"]] == \
        ["general dimensional tolerances"]


THE_SLOT_AND_WHERE_IT_IS_NOT = {
    "datums": [{"name": "the ring axis", "is": "the axis of the \u00d842 bore",
                "fixes": "the length"}],
    "features": [{"id": "left-mounting-slot", "kind": "slot", "topology": "through",
                  "axis": _read("perpendicular to the front face"),
                  "seen_in": ["VIEW B"]}],
    "not_defined": [{"what": "the left mounting-slot lengthwise position from the "
                             "ring axis", "looked_in": ["VIEW B"]}],
}


def test_a_gap_naming_the_datum_it_would_be_measured_from_is_answered_by_nothing():
    """
    A gap says what is missing and then what it belongs to — "the width of the
    ring split" — and where it says the two the other way round it is naming the
    datum the missing value would be measured from. The slot's own axis is not
    where the slot is, and a gap retired by it would take the views it was
    looked in off the Gate with it.
    """
    assert distilled(THE_SLOT_AND_WHERE_IT_IS_NOT)["not_defined"] == \
        THE_SLOT_AND_WHERE_IT_IS_NOT["not_defined"]


def test_two_features_same_axis_same_datum_different_datum_is_a_conflict():
    """
    Two rings running along the depth, agreeing at "the left end face: 65.5 mm"
    and differing at "the bottom face" (25 mm vs 15 mm). One physical ring,
    described twice with different heights.
    """
    model = {
        "features": [
            {
                "id": "bore-ring",
                "kind": "Ø42 through bore",
                "axis": _read("perpendicular to the front view, running front-to-rear (along the depth)"),
                "position": {
                    "the left end face": _read("65.5 mm"),
                    "the bottom face": _read("25 mm"),
                },
            },
            {
                "id": "boss-ring",
                "kind": "Ø50 OD ring",
                "axis": _read("perpendicular to the front face (along the depth)"),
                "position": {
                    "the left end face": _read("65.5 mm"),
                    "the bottom face": _read("15 mm"),
                },
            },
        ],
    }
    grown = merge({}, model)
    about = [conflict["about"] for conflict in grown.get("conflicts", [])]

    assert len(about) == 1, f"Expected 1 conflict, got {len(about)}: {about}"
    assert "bore-ring" in about[0] and "boss-ring" in about[0]

    conflict = grown["conflicts"][0]
    assert conflict["kind"] == "conflict"
    assert "the bottom face" in conflict["stands"] or "the bottom face" in conflict["was_also_said"]


def test_two_features_different_axes_not_a_conflict():
    """
    hole-right has axis perpendicular to top and bottom (height), bore-ring has
    axis along depth. Even though they share "the bottom face: 25 mm" and differ
    at "the right end face", different axes mean not the same placement.
    """
    model = {
        "features": [
            {
                "id": "bore-ring",
                "kind": "Ø42 through bore",
                "axis": _read("along the depth"),
                "position": {
                    "the bottom face": _read("25 mm"),
                    "the right end face": _read("65.5 mm"),
                },
            },
            {
                "id": "hole-right",
                "kind": "Ø6.6 through hole",
                "axis": _read("perpendicular to the top and bottom faces"),
                "position": {
                    "the bottom face": _read("25 mm"),
                    "the right end face": _read("95.5 mm"),
                },
            },
        ],
    }
    grown = merge({}, model)
    conflicts = grown.get("conflicts", [])

    twice_placed_conflicts = [c for c in conflicts if "bore-ring" in c.get("about", "") 
                              and "hole-right" in c.get("about", "")]
    assert len(twice_placed_conflicts) == 0, f"Expected no conflict, got {twice_placed_conflicts}"


def test_reading_2023_clamp_has_no_twice_placed():
    """
    The 2023 clamp reading has 13 features, all distinct. None should trigger
    the twice_placed check.
    """
    with open("tests/recorded/reading_2023_clamp.json") as f:
        model = json.load(f)

    grown = merge({}, model)
    conflicts = grown.get("conflicts", [])

    twice_placed_conflicts = [c for c in conflicts 
                              if "bore-ring" in c.get("about", "") or "boss-ring" in c.get("about", "")]
    assert len(twice_placed_conflicts) == 0, f"Expected no twice_placed conflicts, got {twice_placed_conflicts}"



def test_two_features_same_placeholder_not_a_conflict():
    """
    Two features agreeing on a non-numeric placeholder like "to be read again"
    is not evidence they describe the same placement. Agreement counts only
    for actual measurements, so this does not trigger the check.
    """
    model = {
        "features": [
            {
                "id": "feature-one",
                "kind": "first feature",
                "axis": _read("along the depth"),
                "position": {
                    "the right end face": _read("to be read again off the later page"),
                    "the bottom face": _read("25 mm"),
                },
            },
            {
                "id": "feature-two",
                "kind": "second feature",
                "axis": _read("along the depth"),
                "position": {
                    "the right end face": _read("to be read again off the later page"),
                    "the bottom face": _read("30 mm"),
                },
            },
        ],
    }
    grown = merge({}, model)
    conflicts = grown.get("conflicts", [])

    twice_placed_conflicts = [c for c in conflicts 
                              if "feature-one" in c.get("about", "") 
                              and "feature-two" in c.get("about", "")]
    assert len(twice_placed_conflicts) == 0, (
        f"Expected no conflict (placeholder agreement does not count), "
        f"got {twice_placed_conflicts}"
    )


CLOSED = "closed"


def test_a_kind_off_the_closed_list_is_refused_and_the_field_stays_empty():
    """
    ADR-0026/FCC-232: an `offered` fragment marking itself `"kind_vocabulary":
    "closed"` has every feature's `kind` checked against `design.FEATURE_KIND`
    (22 values). A value off that list is refused -- not written into the
    model -- so the field reads as not yet filled rather than as a value that
    was overruled, the same distinction FCC-94 already keeps for every other
    field.
    """
    grown = merge({}, {"kind_vocabulary": CLOSED,
                        "features": [{"id": "gizmo-1", "kind": "gizmo"}]})

    assert grown["features"][0].get("kind") is None
    conflict = grown["conflicts"][0]
    assert conflict["about"] == "features → gizmo-1 → kind"
    assert conflict["kind"] == design.A_CONFLICT


def test_a_refused_kind_names_the_banned_synonym_it_maps_onto():
    """
    A refusal that only says "invalid" gives the next turn no reason to
    answer differently, and burns the revision budget re-asking for a value
    already ruled out (FCC-231's banned-prose-synonym sentences). Naming the
    kind the word maps onto -- `round` is a `fillet`, `recess` is a
    `counterbore` -- is correctable in one turn.
    """
    grown = merge({}, {"kind_vocabulary": CLOSED,
                        "features": [{"id": "corner-1", "kind": "round"}]})

    stands = grown["conflicts"][0]["stands"]
    assert "round" in stands and "fillet" in stands


def test_a_refused_region_word_names_pad_and_the_region_field():
    """
    A region word (`ring`, `arm`, `web`, ...) is the open-set leak ADR-0026
    measured at 18.5% of the corpus -- the refusal for one names both halves
    of the fix: the `kind` is `pad`, and the word itself belongs in the new
    `region` field.
    """
    grown = merge({}, {"kind_vocabulary": CLOSED,
                        "features": [{"id": "ring-1", "kind": "ring"}]})

    stands = grown["conflicts"][0]["stands"]
    assert "pad" in stands and "region" in stands


def test_an_unmarked_offer_takes_the_legacy_unvalidated_path():
    """
    A recorded model has no `kind_vocabulary` marker -- it was written
    against the prose grammar that existed before this ticket. Checking it
    against the closed list would flag most of the corpus and is exactly the
    rejected alternative ADR-0026 names ("a closed list added to the prompts
    and nothing else"); the marker is what tells the two apart.
    """
    grown = merge({}, {"features": [{"id": "arm-1",
                                      "kind": "flat arm running left from the ring"}]})

    assert grown["features"][0]["kind"] == "flat arm running left from the ring"
    assert grown.get("conflicts") in (None, [])


def test_a_closed_kind_that_is_valid_is_written_normally():
    grown = merge({}, {"kind_vocabulary": CLOSED,
                        "features": [{"id": "hole-1", "kind": "hole"}]})

    assert grown["features"][0]["kind"] == "hole"
    assert [c for c in grown.get("conflicts") or []
            if c["about"].endswith("kind")] == []


def test_a_refused_kind_is_a_gap_the_next_turn_may_fill():
    """
    Refusing the write leaves the field unfilled, a gap the next turn may
    close -- not a value already filled that FCC-94's rule then forbids
    changing.
    """
    first = merge({}, {"kind_vocabulary": CLOSED,
                        "features": [{"id": "ring-1", "kind": "ring"}]})

    grown = merge(first, {"kind_vocabulary": CLOSED,
                           "features": [{"id": "ring-1", "kind": "pad",
                                         "region": "ring"}]})

    assert grown["features"][0]["kind"] == "pad"
    assert grown["features"][0]["region"] == "ring"


def test_slot_arm_and_cutout_web_are_caught_by_kind_and_overlapping_datums():
    """
    `docs/research/ste-design-duplicates.md` §4, session
    `e76b61d5bc7143699b451549c90433f7` (the 2023 pipe clamp, design.md's own
    cited case study): `slot-arm` ("slot through the 15 mm arm, 30 mm long
    ... ending at the vertical plane through the ring axis") and `cutout-web`
    ("curved cutout in the web between the top of the arm and the ring") are
    one physical cavity read twice. `tests/recorded/vocabulary_kinds.json`
    records `slot-arm`'s real `position_datums` as `["the ring axis"]` and
    `cutout-web`'s as `[]` — before this ticket the only signal was that
    empty `position`. Here `cutout-web` carries a filled `position` naming
    the same datum, standing for the reconciliation turn design.md already
    asks for reading it again — the pair is caught by equal `kind` (both a
    `slot`, once `kind` is the closed enum) and the shared datum, not by an
    empty `position`.
    """
    model = {
        "features": [
            {"id": "slot-arm", "kind": "slot",
             "position": {"the ring axis": _read("30 mm")}},
            {"id": "cutout-web", "kind": "slot",
             "position": {"the ring axis": _read("coincident")}},
        ],
    }
    grown = merge({}, model)
    about = [c["about"] for c in grown.get("conflicts", []) if c["kind"] == design.A_GAP]

    assert any("slot-arm" in a and "cutout-web" in a for a in about)


def test_slot_and_cutout_blend_are_caught_by_kind_and_overlapping_datums():
    """
    `docs/research/ste-design-duplicates.md` §4, session
    `6d67ee7f5fa4482ebb49495c2f6aef5c`: `slot` ("rectangular slot through the
    arm, 30 mm long, with rounded/filleted ends per VIEW B and the plan") and
    `cutout-blend` ("curved cutout blending the top of the 15 mm arm up into
    the ring") — the same arm/ring junction cavity, two ids, independently of
    the pair above. `vocabulary_kinds.json` records `slot`'s real
    `position_datums` as `["the centre plane of the width", "the ring
    axis"]` and `cutout-blend`'s as `[]`; `cutout-blend` here carries a
    filled `position` at the shared `"the ring axis"` datum, standing for a
    reconciliation read.
    """
    model = {
        "features": [
            {"id": "slot", "kind": "slot",
             "position": {"the centre plane of the width": _read("15 mm"),
                          "the ring axis": _read("coincident")}},
            {"id": "cutout-blend", "kind": "slot",
             "position": {"the ring axis": _read("coincident")}},
        ],
    }
    grown = merge({}, model)
    about = [c["about"] for c in grown.get("conflicts", []) if c["kind"] == design.A_GAP]

    assert any("slot" in a and "cutout-blend" in a for a in about)


def test_bore_upper_and_hole_boss_are_caught_by_kind_and_overlapping_datums():
    """
    `docs/research/ste-design-duplicates.md` §4, session
    `51d7a2f16e224230b5b74f126c7cf8f5`: `bore-upper` and `hole-boss` carry the
    identical `kind` string `"Ø15 through hole in the upper boss"`
    (`hole` in the closed enum), identical `seen_in`, identical diameter, and
    both coaxial with the same boss — `bore-upper` framed off
    `vocabulary_kinds.json`'s real `["the front-to-rear mid-plane", "the left
    end tangent plane"]`, `hole-boss` off its real `["the boss axis"]`. The
    two framings pin the same point and share no datum key as recorded, which
    is exactly why an empty-`position` proxy could never have caught this
    pair — nothing here is empty. Reading `hole-boss` again against
    `bore-upper`'s own framing (a re-ask design.md's reconciliation turn
    exists for) gives it a datum the two share, and the arithmetic catches it.
    """
    model = {
        "features": [
            {"id": "bore-upper", "kind": "hole",
             "position": {"the front-to-rear mid-plane": _read("coincident"),
                          "the left end tangent plane": _read("40 mm")}},
            {"id": "hole-boss", "kind": "hole",
             "position": {"the boss axis": _read("coincident"),
                          "the front-to-rear mid-plane": _read("coincident")}},
        ],
    }
    grown = merge({}, model)
    about = [c["about"] for c in grown.get("conflicts", []) if c["kind"] == design.A_GAP]

    assert any("bore-upper" in a and "hole-boss" in a for a in about)


def test_left_mounting_hole_and_phase2_slot_are_caught_by_kind_and_overlapping_datums():
    """
    `docs/research/ste-design-duplicates.md` §4, session
    `549e6232c9e04b659e5a2d536354bd7a`: page-1 `left-mounting-hole`
    ("Ø6.6 through mounting hole with Ø11 counterbore") and page-2
    `phase2-left-mounting-slot` ("Ø6.6 by 8.0 obround through slot with
    Ø11 counterbore slot") — same mounting location, same fastener
    clearance and counterbore diameter, the page-2 revision turning a round
    hole into an obround one. Geometrically both are the fastener clearance
    hole at that mounting point (`hole` in the closed enum; a structural
    cavity elsewhere in the part is what `slot` names), so kind is equal.
    `vocabulary_kinds.json` records `left-mounting-hole`'s real
    `position_datums` including `"the leftmost tangent plane of the mounting
    arm"`; `phase2-left-mounting-slot`'s real `position_datums` is `[]` — "its
    place is the one already given to `left-mounting-hole`", per the research
    doc. Filling it in with that same datum is that reading made explicit,
    not a proxy on an empty `position`.
    """
    model = {
        "features": [
            {"id": "left-mounting-hole", "kind": "hole",
             "position": {"the leftmost tangent plane of the mounting arm": _read("15 mm")}},
            {"id": "phase2-left-mounting-slot", "kind": "hole",
             "position": {"the leftmost tangent plane of the mounting arm": _read("15 mm")}},
        ],
    }
    grown = merge({}, model)
    about = [c["about"] for c in grown.get("conflicts", []) if c["kind"] == design.A_GAP]

    assert any("left-mounting-hole" in a and "phase2-left-mounting-slot" in a for a in about)


def test_a_dimension_in_kind_is_refused_and_the_dimension_belongs_in_a_field():
    """
    ADR-0026 §6: eleven dimension fields (`diameter`, `outer_diameter`,
    `radius`, `thickness`, `length`, `width`, `depth`, `angle`, `count`,
    `pitch`, `bolt_circle_diameter`) absorb what `kind` prose used to carry.
    A `kind` sentence that still carries a dimension is off the 22-value
    closed enum and is refused the same way any other off-list `kind` is; the
    number belongs in its own field, on the same feature, unaffected by the
    refusal.
    """
    grown = merge({}, {"kind_vocabulary": CLOSED,
                        "features": [{"id": "hole-1", "kind": "Ø40 bore",
                                      "diameter": _read("40 mm")}]})

    feature = grown["features"][0]
    assert feature.get("kind") is None
    assert feature["diameter"] == _read("40 mm")
    assert any(c["about"].endswith("kind") for c in grown["conflicts"])


def test_superseded_by_fires_once_a_duplicate_pair_is_reconciled():
    """
    AC: `superseded_by` fires on the pairs it is built for. Measured before
    this ticket it fired 5 times across 3 models and on none of the 4
    duplicate pairs `twice_over` now finds -- the reconciliation link and the
    defect it exists for never intersected. Declaring `superseded_by` on one
    of `twice_over`'s own pairs, with a position value that disagrees at the
    shared datum, is `unsafe_supersession`'s to catch; and once superseded,
    `twice_over` does not raise the same pair a second time.
    """
    model = {
        "features": [
            {"id": "left-mounting-hole", "kind": "hole",
             "position": {"the leftmost tangent plane of the mounting arm": _read("15 mm")}},
            {"id": "phase2-left-mounting-slot", "kind": "hole",
             "superseded_by": "left-mounting-hole",
             "position": {"the leftmost tangent plane of the mounting arm": _read("18 mm")}},
        ],
    }
    grown = merge({}, model)
    conflicts = grown.get("conflicts", [])

    unsafe = [c for c in conflicts if "synonym pair" in c["about"]]
    assert any("left-mounting-hole" in c["about"] and "phase2-left-mounting-slot" in c["about"]
               for c in unsafe)

    twice_over_hits = [c for c in conflicts if c["kind"] == design.A_GAP
                        and "left-mounting-hole" in c["about"]
                        and "phase2-left-mounting-slot" in c["about"]]
    assert twice_over_hits == []


def test_a_malformed_conflict_entry_is_refused_under_the_kind_vocabulary_marker():
    """
    FCC-237: a Design reply invented four conflict shapes across one recorded
    session, 44 of 46 entries carrying no `what` at all. A reply that opts
    into the closed vocabulary (`kind_vocabulary: closed`) is held to the
    pinned shape — `about`, `stands`, `was_also_said` — and an entry missing
    one of them is refused rather than merged in as-is, exactly as an
    off-vocabulary `kind` is (FCC-231). Nothing the entry said is lost: it
    travels on whole under `was_also_said` of the refusal.
    """
    grown = merge({}, {
        "kind_vocabulary": "closed",
        "conflicts": [{"what": "the split's own width", "note": "ambiguous",
                        "resolution": "left as printed"}],
    })

    assert grown["conflicts"] == [{
        "about": "conflicts → entry 1",
        "stands": "refused — a conflict entry carries about, stands, was_also_said, "
                  "missing about, stands, was_also_said",
        "was_also_said": {"what": "the split's own width", "note": "ambiguous",
                          "resolution": "left as printed"},
        "kind": design.A_CONFLICT,
    }]


def test_a_conflict_entry_carrying_the_pinned_shape_is_merged_as_is():
    grown = merge({}, {
        "kind_vocabulary": "closed",
        "conflicts": [{"about": "envelope \u2192 depth front to rear",
                        "stands": _read("100 mm"), "was_also_said": _read("108 mm")}],
    })

    assert grown["conflicts"] == [{"about": "envelope \u2192 depth front to rear",
                                    "stands": _read("100 mm"),
                                    "was_also_said": _read("108 mm")}]


def test_an_unmarked_reply_keeps_its_conflicts_exactly_as_the_recorded_fixtures_do():
    """The recorded fixtures never sent `kind_vocabulary`, so a malformed
    conflict entry off an unmarked reply is untouched, same as an
    off-vocabulary `kind` is untouched off an unmarked reply."""
    grown = merge({}, {"conflicts": [{"what": "the split's own width"}]})

    assert grown["conflicts"] == [{"what": "the split's own width"}]


RECORDED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recorded")


def _every_conflict_entry():
    """
    Every dict sitting in a `conflicts` list anywhere under `tests/recorded/`
    — the raw model replies in the `.jsonl` transcripts (nested one level
    down, under `result.result`, itself a JSON string) and the merged
    fixtures in the `.json` files alike.
    """
    found = []

    def walk(node):
        if isinstance(node, dict):
            entries = node.get("conflicts")
            if isinstance(entries, list):
                found.extend(entry for entry in entries if isinstance(entry, dict))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for name in os.listdir(RECORDED):
        path = os.path.join(RECORDED, name)
        if name.endswith(".json"):
            with open(path, encoding="utf-8") as handle:
                try:
                    walk(json.load(handle))
                except json.JSONDecodeError:
                    continue
        elif name.endswith(".jsonl"):
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    walk(record)
                    reply = ((record.get("result") or {}).get("result")
                             if isinstance(record.get("result"), dict) else None)
                    if isinstance(reply, str):
                        try:
                            walk(json.loads(reply))
                        except json.JSONDecodeError:
                            pass

    return found


def test_how_many_recorded_conflict_entries_satisfy_the_pinned_shape():
    """
    FCC-237: of every conflict entry `tests/recorded/` carries — the merged
    fixtures and the raw model replies alike — 32 of 38 already carry `about`,
    `stands` and `was_also_said`. The 6 that do not are the raw Design replies
    in `claude-code-design-effort.jsonl`, sent before this ticket pinned a
    shape at all. The number is measured here rather than claimed in a
    docstring.
    """
    entries = _every_conflict_entry()

    assert len(entries) == 38
    matching = [entry for entry in entries
               if all(field in entry for field in design.CONFLICT_ENTRY_FIELDS)]
    assert len(matching) == 32
