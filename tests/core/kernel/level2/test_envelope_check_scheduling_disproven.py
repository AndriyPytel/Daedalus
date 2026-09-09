
"""
FCC-247, second half -- the deterministic envelope check (`the_envelope_
measured`, `core/loop.py:2356`) is free (no model call) where the model-
authored check is not, so the 11.7x cost/benefit ratio that closed the rest
of `check_assertions` (`tests/test_per_step_assertion_scheduling_disproven.
py`) does not reach it -- a free check earns its keep on a single true
positive. This file measures the one thing that decides it anyway: schedule
it to run right after the Step whose own `satisfies` completes the
envelope's addresses, rather than waiting for the run's own `assertion()`,
and does any *later* Step in the same Plan still move the built part's
bounding box? A live-replay `unsafe: True` means that Step's own citations
lied about the part being finished, and the check would fail a part that
was still correctly under construction.

Both live-replay tests here run the real Step code from a real recorded
session (`tests/recorded/envelope_check_scheduling_replay.json`, the script
that built it now gone) through `core.kernel.Kernel`, so the numbers below
are a measured FreeCAD bounding box, not an assumption.

**The verdict.** 1 of 13 qualifying sessions is unsafe
(`f1f13395f7454a53afb790366e03a7ea`): the lid Pad completes both envelope
citations at Step 1, but the un-cited latch Steps after it still grow the
bounding box by 12 mm. A per-Step trigger gated on "this Step's own
citations complete the envelope" would have failed a correct, unfinished
part. FCC-247's free half stays unbuilt for the same reason FCC-240 was
closed: the near-zero running cost does not make a false-fire free, because
a false-fire routes into a revision, and a revision costs roughly a whole
extra attempt. The other session, `a7b95d14ea20491c8dd6c654b3285bf3`, is the
one place in the whole corpus the deterministic check would have been a true
positive -- caught 6 Steps early -- but one true positive against one
demonstrated false-fire mechanism, on a Plan corpus where nothing enforces
that a Step's citations describe every geometric effect it has, is not
enough to schedule a check whose failure mode is silent and structural
rather than occasional bad luck.
"""
import json

import pytest

from core.kernel import Kernel, find_freecadcmd
from tests import modelmania_harness

pytestmark = pytest.mark.freecad

_FIXTURE = modelmania_harness.RECORDED_DIR / "envelope_check_scheduling_replay.json"


def _load_fixture():
    with open(_FIXTURE) as f:
        return json.load(f)


def _bbox_probe():
    return """
_solids = [o for o in doc.Objects if getattr(o, 'Shape', None) is not None
          and o.Shape.Solids and not o.Shape.isNull()]
_shape = max(_solids, key=lambda o: o.Shape.Volume).Shape
_box = _shape.optimalBoundingBox()
_bbox_probe_result = sorted([_box.XLength, _box.YLength, _box.ZLength])
"""


@pytest.fixture(scope="module")
def worker():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def _bbox_after_n_steps(worker, codes, n):
    preamble = "body = doc.getObject(\'Body\')\nif body is None:\n    body = doc.addObject(\'PartDesign::Body\', \'Body\')\n"
    script = preamble + "\n".join(codes[:n]) + _bbox_probe() + "\nraise RuntimeError('BBOX=' + repr(_bbox_probe_result))"
    result = worker.check(script, None)
    marker = "BBOX="
    idx = result.traceback.rfind(marker)
    assert idx != -1, result.traceback
    return eval(result.traceback[idx + len(marker):].strip())


def test_the_corpus_has_exactly_one_unsafe_session_out_of_thirteen():
    fixture = _load_fixture()
    assert len(fixture["candidate_sessions"]) == 13
    assert set(fixture["step_codes"]) == {
        "a7b95d14ea20491c8dd6c654b3285bf3",
        "f1f13395f7454a53afb790366e03a7ea",
    }


def test_a7b95d14_envelope_is_already_wrong_six_steps_before_assertion_runs(worker):
    fixture = _load_fixture()
    case = fixture["step_codes"]["a7b95d14ea20491c8dd6c654b3285bf3"]
    assert case["n_steps"] == 7
    assert case["env_complete_idx"] == 0

    bbox_at_step0 = _bbox_after_n_steps(worker, case["codes"], case["env_complete_idx"] + 1)

    target = sorted([9.30, 16.00, 17.77])
    delta = max(abs(a - b) for a, b in zip(bbox_at_step0, target))
    assert delta > 0.5, (bbox_at_step0, target, delta)


def test_f1f13395_a_later_step_still_moves_the_bounding_box_after_citations_complete(worker):
    fixture = _load_fixture()
    case = fixture["step_codes"]["f1f13395f7454a53afb790366e03a7ea"]
    assert case["n_steps"] == 10
    assert case["env_complete_idx"] == 1

    bbox_at_complete = _bbox_after_n_steps(worker, case["codes"], case["env_complete_idx"] + 1)
    bbox_at_end = _bbox_after_n_steps(worker, case["codes"], case["n_steps"])

    delta = max(abs(a - b) for a, b in zip(bbox_at_complete, bbox_at_end))
    assert delta > 0.5, (bbox_at_complete, bbox_at_end, delta)
