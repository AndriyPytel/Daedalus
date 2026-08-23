"""
Review is the phase that must not be able to find anything out.

ADR-0003 gives Review the Intent and the outcome and withholds the Plan and the
code, so the judgement is unbiased. Every capability this epic adds is a way to
find something out, which for Review is the failure mode rather than the point:
an action that reads the Document, the Plan or the Steps hands Review exactly
the knowledge ADR-0003 removed.

That isolation is structural today - `review()` takes no tool registry, so there
is nothing to act with. Structural is worth keeping, and worth failing loudly if
someone wires it up without reading ADR-0003 first.
"""
import inspect

from core import loop


def test_review_is_handed_no_way_to_act():
    taken = set(inspect.signature(loop.review).parameters)

    assert taken == {
        "intent",
        "views",
        "described",
        "llm",
        "events",
        "attachments",
        "checked",
    }
    assert not taken & {"tools", "budgets", "agentic", "namespace", "traps"}
