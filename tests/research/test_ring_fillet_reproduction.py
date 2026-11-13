"""
Reproduces a fillet body's own recompute against a real headless FreeCAD:
Steps 1-16 rebuild the ring's base body and must match its logged volume
within 1 mm3, Steps 1-26 build in the same Kernel session the worker uses
in production, and Step 35's front boundary loop builds one Fillet per
`edges_by_concavity` group with the Body left one valid solid.
"""
import json
import os

import pytest

import ring_fillet_classification as classification
from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
EXPECTED_VOLUME_MM3 = 319904.85


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel(timeout=300) as running:
        yield running


def _load(name):
    with open(os.path.join(FIXTURES, name)) as fh:
        return json.load(fh)


def test_steps_1_to_16_rebuild_the_logged_volume(kernel):
    """AC: Steps 1-16 reproduce the log's body, volume within 1 mm3."""
    design = _load("ring_fillet_design.json")
    steps = _load("ring_fillet_steps.json")

    boot = kernel.rehearse("doc.addObject('PartDesign::Body', 'Body')")
    assert boot.ok, boot.traceback

    for n in range(1, 17):
        result = kernel.rehearse(classification.with_design(design, steps[str(n)]))
        assert result.ok, "Step %d failed: %s" % (n, result.traceback)

    volume = kernel.rehearse("print(doc.getObject('Body').Shape.Volume)")
    assert volume.ok, volume.traceback
    assert abs(float(volume.stdout.strip()) - EXPECTED_VOLUME_MM3) < 1.0


def test_steps_1_to_26_build_as_one_kernel_session(kernel):
    """AC: Steps 1-26, including the 10-fillet Step 17-26 block as one Kernel.rehearse() call, complete."""
    design = _load("ring_fillet_design.json")
    steps = _load("ring_fillet_steps.json")
    fillet_1730_code = _load("ring_fillet_steps_17_30.json")["code"]
    classification._build_steps_1_to_26(kernel, design, steps, fillet_1730_code)


def test_step_27_and_31_exclude_each_other():
    """AC: Step 27 (outer edge) and Step 31 (bore edge) on one opening exclude each other, either build order."""
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    classification.check_step_27_and_31_exclude_each_other()


def test_step_35_groups_all_build_and_body_stays_one_valid_solid(kernel):
    """AC1: Step 35's front boundary loop, split by edges_by_concavity, builds every group and the Body stays one valid solid."""
    design = _load("ring_fillet_design.json")
    steps = _load("ring_fillet_steps.json")
    fillet_1730_code = _load("ring_fillet_steps_17_30.json")["code"]

    total_edges, group_results, is_valid, solid_count = classification.build_step_35_split_by_concavity(
        kernel, design, steps, fillet_1730_code
    )

    assert group_results, "expected at least one concavity group, got none for %d edges" % total_edges
    for size, ok in group_results:
        assert ok, "a Step 35 group of %d edges failed to build: %r" % (size, group_results)
    assert is_valid, "Body was left invalid after Step 35's groups built"
    assert solid_count == 1, "Body was left with %d solids after Step 35's groups built" % solid_count
