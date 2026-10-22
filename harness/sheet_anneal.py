"""
Simulated annealing of dimension placements: every unit offers options (zero-argument builders of a candidate with texts, segments, leader length and shift from its preferred spot) and the state is one option per unit.
The cost is 100 per text on a text, 50 per text on a line that is not its own, 30 per text on an outline or the frame, 1 per mm of leader and 5 per mm of displacement.
The run is seeded and deterministic, starts from the given state and returns the best state seen, so it is never worse than the start.
"""
import math
import random

import numpy as np

from sheet_geometry import AREA, KEEP_OUT, PAD, inside, rects_meet, segments_meet_rect

TEXT_ON_TEXT = 100
TEXT_ON_LINE = 50
TEXT_ON_OUTLINE = 30
LEADER = 1
DISPLACEMENT = 5
MOVES_PER_UNIT = 2000
START_TEMPERATURE = 40.0
END_TEMPERATURE = 0.2
FAVOURED = 40
OUTLINE_OWNER = -1


def _bbox(candidate):
    boxes = [t["rect"] for t in candidate["texts"]]
    segments = candidate["segments"]
    if len(segments):
        boxes.append((
            min(segments[:, 0].min(), segments[:, 2].min()) - PAD, min(segments[:, 1].min(), segments[:, 3].min()) - PAD,
            max(segments[:, 0].max(), segments[:, 2].max()) + PAD, max(segments[:, 1].max(), segments[:, 3].max()) + PAD,
        ))
    boxes = np.array(boxes)
    return np.array([boxes[:, 0].min(), boxes[:, 1].min(), boxes[:, 2].max(), boxes[:, 3].max()])


def _unary(candidate, fixed):
    cost = LEADER * candidate.get("leader", 0.0) + DISPLACEMENT * candidate.get("shift", 0.0)
    for text in candidate["texts"]:
        rect, owner = text["rect"], text["owner"]
        if not inside(rect, AREA) or rects_meet(rect, KEEP_OUT):
            cost += TEXT_ON_OUTLINE
        cost += TEXT_ON_TEXT * sum(1 for o in fixed.texts if o["owner"] != owner and rects_meet(rect, o["rect"]))
        ids = fixed.near(rect)
        if len(ids):
            meets = segments_meet_rect(fixed.segments[ids], rect) & (fixed.segment_owner[ids] != owner)
            for o in np.unique(fixed.segment_owner[ids][meets]):
                cost += TEXT_ON_OUTLINE if o == OUTLINE_OWNER else TEXT_ON_LINE
    return cost


def _text_on_lines(texts, segments):
    if not len(segments):
        return 0
    return sum(1 for t in texts if segments_meet_rect(segments, t["rect"]).any())


def _pair(a, b):
    cost = TEXT_ON_TEXT * sum(1 for s in a["texts"] for t in b["texts"] if rects_meet(s["rect"], t["rect"]))
    return cost + TEXT_ON_LINE * (_text_on_lines(a["texts"], b["segments"]) + _text_on_lines(b["texts"], a["segments"]))


class _Pool:
    def __init__(self, units, fixed):
        self.units, self.fixed, self.built = units, fixed, {}

    def get(self, i, k):
        key = (i, k)
        if key not in self.built:
            candidate = self.units[i]["options"][k]()
            candidate["box"] = _bbox(candidate)
            candidate["unary"] = _unary(candidate, self.fixed)
            self.built[key] = candidate
        return self.built[key]


def _against(candidate, i, chosen, boxes):
    box = candidate["box"]
    near = (boxes[:, 0] < box[2]) & (boxes[:, 2] > box[0]) & (boxes[:, 1] < box[3]) & (boxes[:, 3] > box[1])
    near[i] = False
    return candidate["unary"] + sum(_pair(candidate, chosen[j]) for j in np.nonzero(near)[0])


def _total(chosen, boxes):
    cost = 0.0
    for i, candidate in enumerate(chosen):
        cost += candidate["unary"]
        box = candidate["box"]
        near = (boxes[i + 1:, 0] < box[2]) & (boxes[i + 1:, 2] > box[0]) & (boxes[i + 1:, 1] < box[3]) & (boxes[i + 1:, 3] > box[1])
        cost += sum(_pair(candidate, chosen[i + 1 + j]) for j in np.nonzero(near)[0])
    return cost


def _state(pool, choices):
    chosen = [pool.get(i, k) for i, k in enumerate(choices)]
    boxes = np.array([c["box"] for c in chosen]).reshape(-1, 4)
    return chosen, boxes


def state_cost(units, fixed, choices):
    chosen, boxes = _state(_Pool(units, fixed), choices)
    return _total(chosen, boxes)


def anneal(units, fixed, start, seed=0, moves_per_unit=MOVES_PER_UNIT):
    """(choices, cost) of the best state seen from `start` over moves_per_unit x len(units) random single-unit changes."""
    pool = _Pool(units, fixed)
    chosen, boxes = _state(pool, start)
    choices = list(start)
    cost = _total(chosen, boxes)
    best, best_cost = list(choices), cost
    rng = random.Random(seed)
    movable = [i for i, u in enumerate(units) if len(u["options"]) > 1]
    steps = moves_per_unit * len(movable)
    cooling = (END_TEMPERATURE / START_TEMPERATURE) ** (1 / max(steps, 1))
    temperature = START_TEMPERATURE
    for _ in range(steps):
        temperature *= cooling
        if best_cost == 0:
            break
        i = rng.choice(movable)
        count = len(units[i]["options"])
        k = rng.randrange(min(count, FAVOURED)) if rng.random() < 0.5 else rng.randrange(count)
        if k == choices[i]:
            continue
        new = pool.get(i, k)
        delta = _against(new, i, chosen, boxes) - _against(chosen[i], i, chosen, boxes)
        if delta <= 0 or rng.random() < math.exp(-delta / temperature):
            choices[i], chosen[i], boxes[i] = k, new, new["box"]
            cost += delta
            if cost < best_cost - 1e-9:
                best, best_cost = list(choices), cost
    return best, best_cost
