import numpy as np

import sheet_anneal as sa
import sheet_render as sr


def candidate(owner, rect, segments=()):
    text = {"rect": rect, "label": f"t{owner}", "owner": owner}
    return sr._candidate(owner, [], [text], segments, [])


def unit(owner, *rects):
    return {"owner": owner, "options": [(lambda r=r: candidate(owner, r)) for r in rects]}


def fixed_with_line(owner, segment):
    items = sr.Items()
    items.add_segments([segment], owner)
    return items


def test_a_planted_text_on_a_line_is_moved_off_it():
    items = fixed_with_line(9, [40, 52, 70, 52])
    units = [unit(0, (50, 50, 60, 54), (50, 100, 60, 104))]
    choices, cost = sa.anneal(units, items, [0], seed=1)
    assert choices == [1]
    assert cost == sa.state_cost(units, items, [1]) < sa.state_cost(units, items, [0])


def test_two_texts_on_each_other_are_separated():
    items = sr.Items()
    clash = (50, 50, 60, 54)
    units = [unit(0, clash, (50, 100, 60, 104)), unit(1, clash, (80, 100, 90, 104))]
    choices, _ = sa.anneal(units, items, [0, 0], seed=1)
    assert sa.state_cost(units, items, choices) == 0


def test_the_annealer_never_ends_worse_than_the_start_and_is_deterministic():
    rng = np.random.default_rng(3)
    items = fixed_with_line(9, [30, 60, 120, 60])
    units = []
    for owner in range(8):
        rects = [(x, y, x + 12, y + 4) for x, y in rng.uniform(30, 120, (15, 2))]
        units.append(unit(owner, *rects))
    start = [0] * 8
    first = sa.anneal(units, items, start, seed=7)
    again = sa.anneal(units, items, start, seed=7)
    assert first == again
    assert first[1] <= sa.state_cost(units, items, start)
