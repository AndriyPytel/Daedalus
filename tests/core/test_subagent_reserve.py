"""
`SessionTreeSupervisor.reserved()` crosses ahead of `outcome` itself: it is
true once fewer than `RESERVE` turns remain before `turn_budget`, so a
caller can force the choice `outcome` only forces once the wall is already
behind it.
"""
from core.agent import subagent as sa


def test_a_supervisor_with_room_past_the_reserve_is_not_reserved():
    supervisor = sa.SessionTreeSupervisor(turn_budget=10)
    for _ in range(10 - sa.RESERVE - 1):
        supervisor.record_turn()
    assert supervisor.outcome is None
    assert supervisor.reserved() is False


def test_a_supervisor_at_the_reserve_boundary_is_reserved_but_not_exhausted():
    supervisor = sa.SessionTreeSupervisor(turn_budget=10)
    for _ in range(10 - sa.RESERVE):
        supervisor.record_turn()
    assert supervisor.outcome is None
    assert supervisor.reserved() is True


def test_a_supervisor_already_exhausted_is_not_reserved_it_is_outcome():
    supervisor = sa.SessionTreeSupervisor(turn_budget=1)
    supervisor.record_turn()
    supervisor.record_turn()
    assert supervisor.outcome == sa.BUDGET_EXHAUSTED
    assert supervisor.reserved() is False
