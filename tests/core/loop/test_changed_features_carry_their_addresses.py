"""
On an edit every `changed` feature carries `at`, the base-part addresses it is or changes, copied from the inventory; a feature with none, or with one the inventory does not list, is asked again like a Design that changes nothing.
"""
from core.agent import phase_contract as pc
from core.design.presence_checks import unplaced_changes
from core.events import EventStream
from core.loop import describe as describe_module
from core.loop.budgets import Budgets
from core.loop.execute import _changed_addresses
from core.loop.prompt import prompt

CONE = "cone axis (0, 0, 1), apex (0, 0, -142.768), angle 45"

FACES = {"plane y = -17.5": 1605.35, "plane y = -177.5": 29511.0,
         "boss diameter 110, axis (0, 1, 0), at (-96.5145, 0, -4.7774)": 3000.0, CONE: 478.9}

for_the_run = pc.the_base_faces


def _feature(identity, edit, at=None):
    return dict({"id": identity, "edit": edit}, **({"at": at} if at is not None else {}))


def test_an_inventory_cut_at_forty_lines_does_not_hide_the_cone_from_at():
    faces = {"plane z = %d" % n: 100.0 - n for n in range(60)}
    faces[CONE] = 478.9
    part = {"features": [_feature("fillet-bottom", "changed", [CONE])]}

    assert unplaced_changes(part, faces) == []


def test_a_changed_feature_at_a_listed_address_is_placed():
    part = {"features": [_feature("boss-centre", "changed", ["plane y = -17.5"]),
                         _feature("fillet-bottom", "changed", ["cone axis (0, 0, 1), apex (0, 0, -142.768), angle 45"]),
                         _feature("pad-outer", "kept")]}

    assert unplaced_changes(part, FACES) == []


def test_a_changed_feature_without_at_is_a_finding():
    assert unplaced_changes({"features": [_feature("boss-centre", "changed")]}, FACES)


def test_an_at_the_inventory_does_not_list_is_a_finding():
    found = unplaced_changes({"features": [_feature("boss-centre", "changed", ["plane y = -17.6"])]}, FACES)

    assert found and "plane y = -17.6" in found[0]["was_also_said"]


def test_the_whitespace_of_an_address_is_not_part_of_it():
    assert unplaced_changes({"features": [_feature("a", "changed", ["plane  y = -17.5"])]}, FACES) == []


def test_the_changed_addresses_are_the_union_of_at_and_the_ids_are_not_read():
    design = {"features": [_feature("boss-centre", "changed", ["plane y = -17.5"]),
                           _feature("fillet-bottom", "changed", ["cone axis (0, 0, 1), apex (0, 0, -142.768), angle 45"]),
                           _feature("x @ plane y = -1", "kept", ["plane y = -2"]),
                           _feature("pin @ plane y = -3", "changed")]}

    assert _changed_addresses(design) == {"plane y = -17.5",
                                          "cone axis (0, 0, 1), apex (0, 0, -142.768), angle 45"}


def test_the_design_prompt_asks_for_at_on_a_changed_feature():
    said = " ".join(prompt("design").split())

    assert '"at"' in said and "the_base_part" in said


def _asked(monkeypatch, answers):
    asked = []

    def design(body, *args, **kwargs):
        asked.append(body)
        return answers.pop(0) if answers else {}, ""

    monkeypatch.setattr(describe_module, "design", design)
    return asked


def _marked(part):
    with for_the_run(FACES):
        return describe_module._the_change_is_marked(
            part, "move the bosses", [], None, EventStream(), Budgets(), lambda: False,
            base="planes:\n  plane y = -17.5 (area 1 mm2)")


def test_a_changed_feature_with_no_at_is_asked_again_and_the_answer_places_it(monkeypatch):
    asked = _asked(monkeypatch, [{"features": [{"id": "boss-centre", "at": ["plane y = -17.5"]}]}])

    placed = _marked({"features": [_feature("boss-centre", "changed")]})

    assert len(asked) == 1 and "at" in asked[0]["the_question"]
    assert not placed.get("conflicts")


def test_an_at_nobody_supplies_is_refused_after_the_rereads(monkeypatch):
    asked = _asked(monkeypatch, [])

    refused = _marked({"features": [_feature("boss-centre", "changed")]})

    assert len(asked) == Budgets().rereads
    assert any("at" in finding["about"] for finding in refused["conflicts"])
