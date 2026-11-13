"""
Level 2 -- each verb of the Kernel once, against a real `freecadcmd`: `seed` and `rehearse` start and keep a session, `probe` leaves the Document as it was, `render` draws it and `measure` returns what a script printed.
"""
import shutil

import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad

BOX = "doc.addObject('Part::Box', 'Box')"


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        assert running.seed().ok
        yield running


def _names(kernel):
    return kernel.measure(
        "import json\nprint('FACTS ' + json.dumps({'names': sorted(o.Name for o in doc.Objects)}))")["names"]


def test_measure_returns_facts_from_a_script(kernel):
    facts = kernel.measure("import json\nprint('noise')\nprint('FACTS ' + json.dumps({'solid': True, 'n': 3}))")

    assert facts == {"solid": True, "n": 3}


def test_a_script_too_long_for_the_output_still_measures(kernel):
    facts = kernel.measure(
        "import json\nwith open(FACTS_PATH, 'w') as out:\n    json.dump({'long': 'x' * 9000}, out)\nprint('FACTS @')")

    assert facts == {"long": "x" * 9000}


def test_probe_rolls_back_and_rehearse_keeps(kernel):
    probed = kernel.probe(BOX)

    assert probed.ok and probed.rolled_back == ["Box"]
    assert _names(kernel) == []

    kept = kernel.rehearse(BOX)

    assert kept.ok and kept.created == ["Box"]
    assert _names(kernel) == ["Box"]


def test_seed_loads_the_document_the_path_names(kernel, tmp_path):
    kept = tmp_path / "kept.FCStd"
    shutil.copy(kernel.rehearse(BOX).checkpoint, kept)
    assert kernel.seed().ok
    assert _names(kernel) == []

    assert kernel.seed(str(kept)).ok
    assert _names(kernel) == ["Box"]


def test_render_draws_the_document_into_the_path(kernel, tmp_path):
    kernel.rehearse(BOX)
    picture = tmp_path / "view.png"

    drawn = kernel.render(str(picture), "front")

    assert drawn.ok and drawn.render == str(picture) and picture.exists()
