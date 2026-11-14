"""
Level 1 -- every helper the Part prompt tells the model to call is one the Step namespace holds.
"""
import re

from core.kernel.step_namespace import HELPERS
from core.prompts import read_prompt


def test_the_prompt_names_only_namespace_helpers():
    prompt = read_prompt("reader.md")
    scope = prompt.split("## Python scope")[1].split("\n## ")[0]
    named = set(re.findall(r"`(\w+)\(", scope))

    assert named
    assert named <= {helper.__name__ for helper in HELPERS}
