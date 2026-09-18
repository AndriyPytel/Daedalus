"""
Level 2 -- the one-time human check re-run as a measurement: every fact
`manual/modelmania/exemplar_facts.json` claims about the six Exemplar STEPs
is recomputed here from the real files, not read back from its own record.
"""
import os

import pytest

from tests import modelmania_harness

pytestmark = pytest.mark.freecad


def _measure(step_path):
    import Part

    from core.topology import betti_numbers

    shape = Part.Shape()
    shape.read(str(step_path))
    bbox = shape.BoundBox
    deflection = 0.001 * bbox.DiagonalLength
    points, facets = shape.tessellate(deflection)
    vertices = [(p.x, p.y, p.z) for p in points]
    return {
        "volume": round(shape.Volume, 1),
        "bbox": [round(bbox.XLength, 1), round(bbox.YLength, 1), round(bbox.ZLength, 1)],
        "face_count": len(shape.Faces),
        "solid_count": len(shape.Solids),
        "watertight": shape.Shells[0].isClosed(),
        "betti": list(betti_numbers(vertices, facets)),
    }


def _require_exemplar_dir():
    if not modelmania_harness.EXEMPLAR_DIR.is_dir():
        pytest.skip("modelmania exemplars not found; set FCC_MODELMANIA_DIR")


@pytest.mark.parametrize("stem", sorted(modelmania_harness.exemplar_facts()))
def test_the_recorded_facts_match_the_real_exemplar_step(stem):
    _require_exemplar_dir()
    facts = modelmania_harness.exemplar_facts()[stem]
    measured = _measure(modelmania_harness.exemplar_step(stem))

    assert measured == {k: v for k, v in facts.items() if k != "sheet_envelope_mm"}


def test_every_exemplar_step_has_a_recorded_row():
    _require_exemplar_dir()
    stems = {name[:-len(".step")] for name in os.listdir(modelmania_harness.EXEMPLAR_DIR)
             if name.endswith(".step")}

    assert set(modelmania_harness.exemplar_facts()) == stems
