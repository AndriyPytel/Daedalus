from core.freecad_apply import RETIRED_TRAPS, SEED_TRAPS, TRAPS


def test_every_seed_trap_carries_a_script():
    """A seed trap without a script belongs in RETIRED_TRAPS, not here."""
    assert all(trap.script for trap in SEED_TRAPS)


def test_a_retired_trap_carries_the_reason_it_was_dropped():
    """A trap whose script could not be written is retired with why, not silently gone."""
    assert RETIRED_TRAPS
    for claim, reason in RETIRED_TRAPS:
        assert claim not in TRAPS
        assert reason
