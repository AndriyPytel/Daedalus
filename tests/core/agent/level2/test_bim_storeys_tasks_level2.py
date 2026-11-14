"""
Level 2 -- every storeys task of the BIM benchmark reopens, and its reference building passes the Assertion its text asks for.
"""
import os
import sys

import pytest

from core.agent.bim import FACTS_CHECK, refusals
from core.kernel import Kernel, find_freecadcmd

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, os.path.join(ROOT_DIR, "src", "tools"))

import lane  # noqa: E402

pytestmark = pytest.mark.freecad

ROOT = os.path.join(lane.bim_dir(), "storeys")
if not os.path.isdir(ROOT):
    pytest.skip("test-data/bim/storeys not built", allow_module_level=True)


@pytest.fixture(scope="module")
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel(timeout=180) as running:
        yield running


@pytest.mark.parametrize("name", sorted(os.listdir(ROOT)) if os.path.isdir(ROOT) else [])
def test_the_reference_reopens_and_passes_the_assertion(kernel, name):
    folder = os.path.join(ROOT, name)
    assert kernel.seed(os.path.join(folder, "reference.FCStd")).ok

    found = refusals(kernel.measure(FACTS_CHECK))

    assert found == [], found
