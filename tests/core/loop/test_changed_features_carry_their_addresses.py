"""
On an edit every `changed` feature carries `at`, the base-part addresses it is or changes, copied from the inventory; a feature with none, or with one the inventory does not list, is asked again like a Design that changes nothing.
"""
from core.design.presence_checks import listed_addresses, unplaced_changes
from core.events import EventStream
from core.loop import describe as describe_module
from core.loop.budgets import Budgets
from core.loop.execute import _changed_addresses
from core.loop.prompt import prompt

BASE = """The part before the edit, in mm:
planes:
  plane y = -17.5 (area 1605.35 mm2)
  plane y = -177.5 (area 29511 mm2)
cylinders:
  boss diameter 110, axis (0, 1, 0), at (-96.5145, 0, -4.7774) (area 3000 mm2)
other surfaces:
  cone axis (0, 0, 1), apex (0, 0, -142.768), angle 45 (area 478.9 mm2)
  ... and 3 more"""


def _feature(identity, edit, at=None):
    return dict({"id": identity, "edit": edit}, **({"at": at} if at is not None else {}))


def test_the_inventory_lines_are_the_addresses_without_their_area():
    listed = listed_addresses(BASE)

    assert "plane y = -17.5" in listed
    assert "cone axis (0, 0, 1), apex (0, 0, -142.768), angle 45" in listed
    assert not any("area" in line or "more" in line or "mm," in line.split("diameter")[0] for line in listed)


def test_a_changed_feature_at_a_listed_address_is_placed():
    part = {"features": [_feature("boss-centre", "changed", ["plane y = -17.5"]),
                         _feature("fillet-bottom", "changed", ["cone axis (0, 0, 1), apex (0, 0, -142.768), angle 45"]),
                         _feature("pad-outer", "kept")]}

    assert unplaced_changes(part, BASE) == []


def test_a_changed_feature_without_at_is_a_finding():
    assert unplaced_changes({"features": [_feature("boss-centre", "changed")]}, BASE)


def test_an_at_the_inventory_does_not_list_is_a_finding():
    found = unplaced_changes({"features": [_feature("boss-centre", "changed", ["plane y = -17.6"])]}, BASE)

    assert found and "plane y = -17.6" in found[0]["was_also_said"]


def test_the_whitespace_of_an_address_is_not_part_of_it():
    assert unplaced_changes({"features": [_feature("a", "changed", ["plane  y = -17.5"])]}, BASE) == []


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
    return describe_module._the_change_is_marked(
        part, "move the bosses", [], None, EventStream(), Budgets(), lambda: False, base=BASE)


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
