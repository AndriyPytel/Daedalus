"""
The Part Domain refuses `finish` while the grader could not mesh the Document's solid.
"""
from types import SimpleNamespace

from core.agent import reader


class Kernel:
    def __init__(self, mesh):
        self.mesh = mesh
        self.timeouts = []

    def check(self, code, **options):
        self.timeouts.append(options.get("timeout"))
        if "TREE " in code:
            return SimpleNamespace(stdout='TREE [{"name": "Body", "type": "PartDesign::Body", '
                                          '"tip": "PartDesign::Pad", "features": ["PartDesign::Pad"]}]')
        return SimpleNamespace(stdout="MESH " + self.mesh)


def test_a_solid_the_grader_cannot_mesh_is_refused_with_the_reason():
    refused = reader.unfinished_part(Kernel('"fillet radius 0.50 mm"'))

    assert "fillet radius 0.50 mm" in refused


def test_a_solid_the_grader_can_mesh_is_not_refused():
    assert reader.unfinished_part(Kernel("null")) is None


def test_the_kernel_is_given_longer_than_the_meshing_limit():
    kernel = Kernel("null")

    reader.unfinished_part(kernel)

    assert kernel.timeouts[-1] > 60


def test_a_check_that_could_not_run_does_not_refuse_the_finish():
    assert reader.unmeshable(SimpleNamespace(check=lambda code, **options: SimpleNamespace(stdout=""))) is None
