import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS))

LOCAL_DESIGNS = Path("/Users/apytel/Projects/FreeCadCopilot/harness/data/fusion360")


@pytest.fixture
def local_design():
    path = LOCAL_DESIGNS / "22323_aa6edb8b_0002"
    if not path.exists():
        pytest.skip("harness/data/fusion360 is git-excluded and absent here")
    return path
