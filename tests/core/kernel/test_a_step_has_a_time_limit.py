"""
Level 1 -- a Step that took over the limit on the live Document is rolled back and refused, with the clock injected.
"""
from core.freecad_apply import STEP_LIMIT, ApplyPipeline
from core.kernel import KernelResult


class Doc:
    def __init__(self):
        self.Objects = []
        self.undone = 0

    def undo(self):
        self.undone += 1


class Worker:
    def check(self, *args, **kwargs):
        return KernelResult(ok=True)


def _apply(took):
    doc = Doc()
    ticks = iter([100.0, 100.0 + took])
    pipeline = ApplyPipeline(document=doc, worker=Worker(), clock=lambda: next(ticks),
                             transaction=lambda *a, **k: doc.Objects.append("Box"))
    result = pipeline.apply("box = 1", kernel_result={"created": ["Box"], "names": {}})
    return doc, result


def test_a_step_over_the_limit_is_undone_and_refused_with_what_to_do():
    doc, result = _apply(STEP_LIMIT + 1)

    assert not result.ok and result.gate == "too_slow"
    assert "this step took over 120 s; build it with fewer elements or a pattern" in result.traceback
    assert doc.undone == 1


def test_a_step_within_the_limit_lands():
    doc, result = _apply(STEP_LIMIT - 1)

    assert result.ok and doc.undone == 0
