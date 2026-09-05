"""
`tools/ab_repair.py` split-log repair: the double `json.loads` per kept line
proven fixed (FCC-287d).
"""
import json

from tools import ab_repair


def test_kept_cost_parses_each_line_once():
    """
    The old `kept = sum(json.loads(l)[...] for l in parts[-1] if json.loads(l)[...])`
    called `json.loads` twice per line — once in the filter, once in the
    value. `_kept_cost` must call it exactly once per line.
    """
    lines = [
        json.dumps({"kind": "cost", "data": {"usd": 0.5}}) + "\n",
        json.dumps({"kind": "step_started", "data": {}}) + "\n",
        json.dumps({"kind": "cost", "data": {"usd": 1.25}}) + "\n",
    ]

    calls = []
    real_loads = json.loads

    def counting_loads(text):
        calls.append(text)
        return real_loads(text)

    json.loads = counting_loads
    try:
        total = ab_repair._kept_cost(lines)
    finally:
        json.loads = real_loads

    assert total == 1.75
    assert len(calls) == len(lines), "each line must be parsed exactly once"
