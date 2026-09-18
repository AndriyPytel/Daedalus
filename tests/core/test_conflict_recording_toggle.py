"""
Level 1 — the off arm of the append-only model: `Budgets.record_conflicts`
reaches the merges `core.loop.describe`/`core.loop.design` do, not just
`core.design.merge` in isolation.
"""
from core.events import DESIGN
from core.loop.budgets import Budgets

from tests.loop_helpers import MODEL_MANIA, design_reply, drive


def _read(value):
    return {"value": value, "flag": "read"}


def _two_disagreeing_reads():
    first = design_reply({"envelope": {"depth front to rear": _read("100 mm")}})
    second = design_reply({"envelope": {"depth front to rear": _read("50 mm")}})
    reconcile = design_reply({})
    return [first, second, reconcile]


def _the_part(events):
    return events.of(DESIGN)[-1].data["model"]


def test_two_disagreeing_reads_are_recorded_by_default():
    _, events, _, _ = drive(
        _two_disagreeing_reads(), attachments=[MODEL_MANIA],
        budgets=Budgets(readings=2))

    the_part = _the_part(events)
    assert the_part.get("conflicts")
    assert the_part["envelope"]["depth front to rear"] == _read("100 mm")


def test_two_disagreeing_reads_drop_only_the_record_with_conflicts_off():
    """
    The off arm keeps the same held value the on arm keeps -- the first
    read stands against the second either way -- and differs only in
    whether that disagreement is recorded.
    """
    _, events, _, _ = drive(
        _two_disagreeing_reads(), attachments=[MODEL_MANIA],
        budgets=Budgets(readings=2, record_conflicts=False))

    the_part = _the_part(events)
    assert not the_part.get("conflicts")
    assert the_part["envelope"]["depth front to rear"] == _read("100 mm")
