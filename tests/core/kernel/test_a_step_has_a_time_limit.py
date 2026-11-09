"""
Level 1 -- a Step that took over the limit on the live Document is said as a WARNING event and still lands, with the clock injected.
"""
from core.events import WARNING, EventStream
from core.freecad_apply import STEP_LIMIT, ApplyPipeline
from core.kernel import KernelResult
from tests.event_helpers import events_of


class Box:
    Name = Label = "Box"
    TypeId = "Part::Box"


class Doc:
    def __init__(self):
        self.Objects = []


class Worker:
    def check(self, *args, **kwargs):
        return KernelResult(ok=True)


def _apply(took):
    doc, events = Doc(), EventStream()
    ticks = iter([100.0, 100.0 + took])
    pipeline = ApplyPipeline(document=doc, worker=Worker(), events=events, clock=lambda: next(ticks),
                             transaction=lambda *a, **k: doc.Objects.append(Box()))
    result = pipeline.apply("box = 1", kernel_result={"created": ["Box"], "names": {}})
    return events, result


def test_a_step_over_the_limit_lands_and_is_said():
    events, result = _apply(STEP_LIMIT + 8)

    assert result.ok
    assert [e.data["message"] for e in events_of(events, WARNING)] == ["step took 128 s on the live Document"]


def test_a_step_within_the_limit_says_nothing():
    events, result = _apply(STEP_LIMIT - 1)

    assert result.ok and not events_of(events, WARNING)
