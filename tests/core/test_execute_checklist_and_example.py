"""
`execute.md` opens with a checklist of the lint rules and one worked Step, so
a cheaper model sees the rules it is held to and one example that passes them
before it writes its own first Step.
"""
import re

import pytest

from core.lint import lint
from core.prompts import read_prompt

EXECUTE = read_prompt("execute.md")

RULES = [
    ("bare numeral in a dimensional position",
     "pad.Length = 80.0\n",
     "bare numeral"),
    ("`.get(key, default)` on a Design field",
     "pad.Length = design['features']['Boss'].get('length', 80.0)\n",
     "resolved from `design` with `.get"),
    ("literal element name",
     "fillet.Base = (box, ['Edge12'])\n",
     "literal element name"),
    ("`Gui.*` or live selection",
     "Gui.ActiveDocument\n",
     "not available during Preflight"),
    ("bare `except`",
     "try:\n    pad.Length = 1\nexcept:\n    pass\n",
     "bare `except`"),
    ("lookup of an object this Step just allocated by its literal name",
     "doc.addObject('PartDesign::Pad', 'Boss')\ndoc.getObject('Boss')\n",
     "was just created by this Step's own"),
]


def _worked_example():
    blocks = re.findall(r"```python\n(.*?)```", EXECUTE, re.DOTALL)
    assert len(blocks) == 1, "execute.md should carry exactly one worked Step example"
    return blocks[0]


def _checklist_lines():
    match = re.search(r"^## Checklist\n(.*?)^## ", EXECUTE, re.DOTALL | re.MULTILINE)
    assert match, "execute.md has no ## Checklist section"
    return [line for line in match.group(1).splitlines() if line.startswith("- ")]


def test_the_checklist_has_five_to_seven_lines():
    lines = _checklist_lines()
    assert 5 <= len(lines) <= 7, "checklist should be 5-7 lines, found %d" % len(lines)


def test_the_worked_example_passes_the_lint():
    result = lint(_worked_example())

    assert result.ok, result.report()


def test_the_worked_example_reads_dimensions_off_design_mm():
    example = _worked_example()

    assert '["mm"]' in example
    assert "design[" in example


def test_the_worked_example_keeps_the_addObject_return_and_checks_with_bbox():
    example = _worked_example()

    assert re.search(r"\w+ = \w+\.newObject\(", example) or "addObject(" in example
    assert "bbox(" in example


def test_the_worked_example_is_declared_with_step():
    assert "declared as Step" in EXECUTE or re.search(r'"step":\s*\d', EXECUTE)


@pytest.mark.parametrize("rule, code, reason_fragment", RULES)
def test_every_checklist_rule_matches_a_refusal_the_lint_makes(rule, code, reason_fragment):
    result = lint(code)

    assert not result.ok, "expected the lint to refuse: %s" % rule
    assert reason_fragment in result.report()


RULE_KEYWORDS = ["numeral", ".get(", "literal element", "Gui", "except", "addObject"]


def test_every_checklist_line_names_one_of_the_tested_rules():
    checklist = " ".join(_checklist_lines())
    for keyword in RULE_KEYWORDS:
        assert keyword in checklist, "no checklist line mentions: %s" % keyword
