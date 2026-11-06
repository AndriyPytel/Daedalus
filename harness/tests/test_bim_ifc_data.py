import pytest

import bim_score
import bim_tasks

ROOT = bim_tasks.OUT
pytestmark = pytest.mark.skipif(not (ROOT / "split.json").exists(), reason="test-data/bim not built")
SIZE_TOLERANCE = 0.05


@pytest.fixture(scope="module")
def audits():
    paths = sorted(ROOT.glob("*/*/*.ifc"))
    return dict(zip(paths, bim_score.audit(paths)))


def test_every_generated_ifc_is_audited(audits):
    assert len(audits) == len(list(ROOT.glob("*/*/*.FCStd")))


def test_every_door_and_window_fills_an_opening(audits):
    assert {p.parent.name + "/" + p.name: a["unfilled"] for p, a in audits.items() if a["unfilled"]} == {}


def test_every_element_sits_in_a_storey(audits):
    assert {p.parent.name + "/" + p.name: a["unplaced"] for p, a in audits.items() if a["unplaced"]} == {}


def test_door_and_window_sizes_agree_with_the_geometry_in_the_files_unit(audits):
    off = {p.parent.name + "/" + p.name: s for p, a in audits.items() for s in a["sizes"] if abs(s["width"] - 1) > SIZE_TOLERANCE or abs(s["height"] - 1) > SIZE_TOLERANCE}
    assert off == {}
