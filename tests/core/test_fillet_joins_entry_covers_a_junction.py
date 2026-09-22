"""
`design.md`'s fillet/chamfer bullet at "the two surfaces it joins" must read
consistently with its `edges` field two paragraphs later: one `joins` entry
covers a whole junction, however many edges the drawing shows there, not one
edge each. `gpt-5.6-luna-high` read the earlier bullet literally and planned
34 Steps for a part the grouped reading builds in 21.
"""
import re

from core.prompts import read_prompt


def _flat(text):
    return re.sub(r"\s+", " ", text)


def test_design_md_no_longer_claims_a_fillet_entry_covers_only_one_edge():
    flat = _flat(read_prompt("design.md"))
    assert "covers more than one edge" not in flat
    assert "is a feature built on one edge" not in flat


def test_design_md_ties_the_joins_bullet_to_the_edges_field():
    flat = _flat(read_prompt("design.md"))
    assert "one entry covers every edge that junction gives" in flat
    assert "`edges` for how many of them the junction gives" in flat


def test_plan_md_worked_example_is_unchanged():
    flat = _flat(read_prompt("plan.md"))
    assert '"round the twelve longitudinal edges of the outer profile at R2"' in flat


def test_execute_md_edges_between_example_is_unchanged():
    flat = _flat(read_prompt("execute.md"))
    assert "`edges_between(shape, a, b)`, which gives the" in flat
    assert ("`edges_between(body.Shape, lambda f: planar_at_z(f, 15), "
            "lambda f: cylinder_of(f, 25))` is the whole of a fillet's "
            "selection") in flat
