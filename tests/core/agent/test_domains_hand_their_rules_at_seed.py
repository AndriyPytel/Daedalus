"""
Level 1 -- each Domain hands the Kernel its rules at seed: the Part Domain the Part rules, the Assembly and BIM Domains none.
"""
import json

from core.agent import part_rules
from core.agent.assembly import run_assembly
from core.agent.bim import run_bim
from core.agent.reader import run_reader
from core.events import EventStream
from core.llm import ScriptedLLMClient


def _seeded_with(run):
    seeds = []
    run(llm=ScriptedLLMClient([json.dumps({"finish": "x"})] * 3), kernel=None, tools=None,
        events=EventStream(), cancelled=lambda: True, start=seeds.append)
    return seeds


def test_the_part_domain_seeds_with_the_part_rules():
    assert _seeded_with(lambda **kw: run_reader("go", **kw)) == [part_rules]


def test_the_assembly_domain_seeds_with_none():
    assert _seeded_with(lambda **kw: run_assembly("go", **kw)) == [None]


def test_the_bim_domain_seeds_with_none():
    assert _seeded_with(lambda **kw: run_bim("go", **kw)) == [None]
