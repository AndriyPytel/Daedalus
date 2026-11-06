"""
Level 1 -- an edit is judged on the whole flat and on having changed the Document; the facts line says what the Document holds; a removal the intent names needs no consent.
"""
from types import SimpleNamespace

import json
import os

from core.agent.bim import describe, opening, refusals
from core.agent.domain import measured
from core.arch import named_in
from tests.bim_helpers import HOLDS


def test_a_document_that_is_still_the_base_is_refused():
    found = refusals(HOLDS, unchanged="base")

    assert len(found) == 1 and "given" in found[0]


def test_a_document_that_differs_from_the_base_is_refused_nothing():
    assert refusals(HOLDS, unchanged="other") == []


def test_a_create_run_has_no_base_to_equal():
    assert refusals(HOLDS) == []


def test_the_facts_line_names_what_the_document_holds():
    facts = HOLDS | {"doors": [{"label": "Door_D1", "hosts": ["Wall_W5"]}],
                     "walls": [HOLDS["walls"][0] | {"label": "Wall_W5"}],
                     "spaces": ["Space_S1"], "windows": [{"label": "Window_WIN3", "hosts": ["Wall_W9"]}]}

    line = describe(facts)

    for label in ("Door_D1", "Wall_W5", "Space_S1", "Window_WIN3", "Wall_W9"):
        assert label in line


def test_the_opening_of_an_edit_carries_the_facts_line_after_the_intent():
    blocks = opening("Remove Window_WIN3.", [], "The Document holds X.")

    assert [b["text"] for b in blocks] == ["Remove Window_WIN3.", "The Document holds X."]


EDIT_02 = "Remove Window_WIN3, the 1.90 m wide window in Wall_W9, and close its opening in the wall."


def _document(objects):
    return SimpleNamespace(getObject=objects.get)


def _object(label, below=(), above=()):
    return SimpleNamespace(Label=label, OutList=list(below), InList=list(above))


def _window():
    proxy = type("Proxy", (), {"__module__": "ArchWall", "Type": "Wall"})()
    sketch, wall = _object("Sketch008"), _object("Wall_W9")
    wall.Proxy = proxy
    window = _object("Window_WIN3", [sketch, wall])
    sketch.InList.append(window)
    wall.InList.append(window)
    return {"Window008": window, "Sketch008": sketch, "Wall008": wall}


def test_the_target_of_a_removal_and_its_own_sketch_may_go():
    assert named_in(EDIT_02, _document(_window()), ["Window008", "Sketch008"])


def test_the_wall_that_hosts_the_target_is_not_approved():
    assert not named_in(EDIT_02, _document(_window()), ["Window008", "Wall008"])


def test_a_wall_named_only_as_context_is_not_approved():
    assert not named_in(EDIT_02, _document(_window()), ["Wall008"])


def test_a_resize_or_a_move_approves_no_deletion():
    objects = _window()

    assert not named_in("Resize Window_WIN3 from 1.63 m to 1.93 m.", _document(objects), ["Window008"])
    assert not named_in("Move Window_WIN3 by 0.30 m.", _document(objects), ["Window008"])


def test_a_replacement_approves_the_object_it_replaces():
    assert named_in("Replace Window_WIN3 with a door.", _document(_window()), ["Window008"])


def test_a_label_inside_a_longer_label_is_not_named():
    assert not named_in("Remove Window_WIN30.", _document(_window()), ["Window008"])


def test_a_name_that_is_no_longer_in_the_document_is_skipped():
    assert named_in(EDIT_02, _document(_window()), ["Window008", "Gone"])


def test_facts_too_long_for_the_kernels_output_are_read_from_the_file_named(tmp_path):
    seen = []

    def check(code, exploring):
        namespace = {}
        exec(code.split("\n")[0], namespace)
        seen.append(namespace["FACTS_PATH"])
        open(namespace["FACTS_PATH"], "w").write(json.dumps({"walls": ["x" * 9000]}))
        return SimpleNamespace(stdout="noise\nFACTS @\n")

    assert measured(SimpleNamespace(check=check), "check") == {"walls": ["x" * 9000]}
    assert not os.path.exists(seen[0])
