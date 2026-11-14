"""
The Part Domain refuses `finish` over one facts dict: the feature tree, the solid count, the grader's mesher and the STEP's read-back.
"""
from core.agent import reader
from core.agent.reader import FACTS_CHECK, refusals
from tests.fake_kernel import FakeKernel

GOOD = {"solid": True, "solids": 1, "tree_faults": [], "extents": [100.0, 50.0, 10.0], "volume": 40000.0,
        "faces": 10, "b0": 1, "b1": 0, "b2": 0, "unmeshable": None, "step_readback": True}


def test_a_finished_part_has_no_refusal():
    assert refusals(GOOD, None, asserting=False) == []


def test_a_solid_the_grader_cannot_mesh_is_refused_with_the_reason():
    found = refusals(dict(GOOD, unmeshable="fillet radius 0.50 mm"), None)

    assert len(found) == 1 and "fillet radius 0.50 mm" in found[0]


def test_a_finish_with_two_solids_is_refused():
    found = refusals(dict(GOOD, solids=2), None)

    assert len(found) == 1 and "2 separate solids" in found[0]


def test_a_step_that_does_not_read_back_valid_is_refused():
    found = refusals(dict(GOOD, step_readback=False), None)

    assert len(found) == 1 and "does not read back valid and manifold" in found[0]


def test_a_step_that_never_got_as_far_as_a_read_back_is_not_refused_for_it():
    assert refusals(dict(GOOD, unmeshable=None, step_readback=None), None) == []


def test_a_solid_that_is_not_a_feature_tree_is_refused_with_the_fault():
    found = refusals({"solid": True, "solids": 1, "tree_faults": ["Solid is a Part::Feature, not a PartDesign::Body"]},
                     None, asserting=False)

    assert len(found) == 1 and "Solid is a Part::Feature" in found[0]


def test_a_document_with_no_solid_is_refused():
    assert "no solid" in refusals({"solid": False}, None)[0]


def test_the_done_check_leaves_the_differences_to_the_assertion():
    unchanged = dict(GOOD, base={"volume": 40000.0, "faces": 10})

    assert refusals(unchanged, None, asserting=False) == []
    assert len(refusals(unchanged, None)) == 1


def test_a_finish_that_fails_is_refused_with_every_line_that_fails():
    refused = reader.unfinished_part(FakeKernel(facts=dict(GOOD, solids=2, step_readback=False)))

    assert refused.startswith("The Part Assertion does not hold:")
    assert "2 separate solids" in refused and "read back" in refused and refused.endswith("Fix this, then finish.")


def test_a_part_that_holds_is_not_refused():
    assert reader.unfinished_part(FakeKernel(facts=GOOD)) is None


def test_a_document_that_could_not_be_measured_is_refused():
    assert "could not be measured" in reader.unfinished_part(FakeKernel(facts=None))


def test_the_kernel_is_given_longer_than_the_meshing_limit():
    kernel = FakeKernel(facts=GOOD)

    reader.unfinished_part(kernel)

    assert kernel.measured[-1][1] > 60


def test_part_finish_makes_one_measure_call():
    refused = FakeKernel(facts=dict(GOOD, solids=2))
    asserted = FakeKernel(facts=GOOD)

    reader.unfinished_part(refused)
    reader.assert_part(asserted, None, None, "/tmp", None)

    assert [script for script, _ in refused.measured] == [FACTS_CHECK]
    assert [script for script, _ in asserted.measured] == [FACTS_CHECK]

