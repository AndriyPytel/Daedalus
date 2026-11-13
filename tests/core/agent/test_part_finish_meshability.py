"""
The Part Domain refuses `finish` while the grader could not mesh the Document's solid.
"""
from core.agent import reader
from tests.fake_kernel import FakeKernel

A_BODY = {"name": "Body", "type": "PartDesign::Body", "tip": "PartDesign::Pad",
          "features": ["PartDesign::Pad"]}


def _kernel(unmeshable):
    return FakeKernel(facts={"solids": [A_BODY], "unmeshable": unmeshable})


def test_a_solid_the_grader_cannot_mesh_is_refused_with_the_reason():
    refused = reader.unfinished_part(_kernel("fillet radius 0.50 mm"))

    assert "fillet radius 0.50 mm" in refused


def test_a_solid_the_grader_can_mesh_is_not_refused():
    assert reader.unfinished_part(_kernel(None)) is None


def test_the_kernel_is_given_longer_than_the_meshing_limit():
    kernel = _kernel(None)

    reader.unfinished_part(kernel)

    assert kernel.measured[-1][1] > 60


def test_a_check_that_could_not_run_does_not_refuse_the_finish():
    assert reader.unmeshable(FakeKernel(facts=None)) is None
