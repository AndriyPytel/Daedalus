"""A `changed` feature whose `at` is no address is asked again with the nearest real addresses of the base part, so Design copies one instead of writing it in its own words."""
from core.design.presence_checks import nearest_addresses
from core.events import DESIGN, EventStream
from core.loop import describe as describe_module
from core.loop import design as design_module
from core.loop.budgets import Budgets
from core.agent.phase_contract import the_base_faces

HOLE = "hole diameter 5, axis (1, 0, 0), at (0, 19.2369, -4.2967)"
FACES = {
    "plane x = -4.9325": 85.332,
    "plane y = -17.5": 1605.35,
    "boss diameter 10, axis (1, 0, 0), at (0, 21.7369, -7.567)": 5.091,
    "boss diameter 5, axis (0.017452, 0, -0.999848), at (-45.1881, 19.2369, -0.7887)": 57.35,
    "hole diameter 1, axis (0.96138, -0.039149, 0.272425), at (1.9029, 12.2144, -4.9601)": 0.005,
    "hole diameter 5, axis (0, 1, 0), at (-3, 0, 12)": 40.0,
    "cone axis (1, 0, 0), apex (-330.738, 16.7369, -1.0265), angle 1": 49.098,
    HOLE: 232.478,
}
PARAPHRASE = "cylinder \u00d85 axis (1,0,0) at (y 19.2369, z -4.2967)"


def _part(at):
    return {"features": [{"id": "boss-left", "edit": "changed", "at": at}]}


def test_the_real_address_is_ranked_first_for_the_cylinder_the_design_wrote_in_its_own_words():
    (feature, entry, nearest), = nearest_addresses(_part([PARAPHRASE]), FACES)

    assert (feature, entry) == ("boss-left", PARAPHRASE)
    assert nearest[0] == HOLE and len(nearest) == 3
    assert not any(address.startswith(("plane", "cone")) for address in nearest)


def test_a_plane_entry_is_matched_to_planes_by_axis_and_offset():
    (_, _, nearest), = nearest_addresses(_part(["plane x = -4.9"]), FACES)

    assert nearest[0] == "plane x = -4.9325"


def test_an_entry_that_is_an_address_or_a_feature_without_at_is_not_listed():
    assert nearest_addresses(_part([HOLE]), FACES) == []
    assert nearest_addresses({"features": [{"id": "a", "edit": "changed"}]}, FACES) == []


def _described(monkeypatch, answers):
    asked = []

    def design(body, *args, **kwargs):
        asked.append(body)
        return (answers.pop(0) if answers else {}), ""

    monkeypatch.setattr(describe_module, "design", design)
    monkeypatch.setattr(design_module, "design", design)
    events = EventStream()
    with the_base_faces(FACES):
        describe_module._describe_the_part("x", None, events, Budgets(), None, lambda: False, [], base="planes:")
    return asked, events


def test_the_re_ask_names_the_feature_and_the_entry_and_lists_the_nearest(monkeypatch):
    asked, _ = _described(monkeypatch, [_part([PARAPHRASE]), _part([HOLE])])
    questions = [body["the_question"] for body in asked
                 if str(body.get("the_question", "")).startswith(describe_module._MARK_WHAT_CHANGES)]

    assert "boss-left" in questions[0] and PARAPHRASE in questions[0]
    assert HOLE in questions[0]


def test_the_design_event_carries_the_full_address_map(monkeypatch):
    _, events = _described(monkeypatch, [_part([HOLE])])

    assert events.last(DESIGN).data["base_faces"] == FACES
