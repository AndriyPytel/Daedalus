"""
Level 1 -- the composed system prompt two consecutive Execute calls of one
pass send, and why it has to be a byte-stable growing prefix for a provider's
own cache to carry the earlier call's cost into the later one.
"""

from core.agent.agent import _composed_system


def test_a_later_calls_system_prompt_carries_the_earlier_one_as_its_prefix():
    """
    The first Execute call of a pass sends no ledger; a Step that fails and
    is retried by a fresh call sends one. The two must still share a whole,
    unchanged prefix, or a provider reading its own cache off the request's
    leading bytes pays for the whole conversation on the second call too.
    """
    first = _composed_system("Write the next Step.")
    second = _composed_system("Write the next Step.",
                               ledger="- Cut the hooks: moved no material")

    assert second.startswith(first)
    assert second != first
