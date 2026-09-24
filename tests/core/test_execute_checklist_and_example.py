"""
`execute.md` opens with a checklist of the lint rules and one worked Step, so
a cheaper model sees the rules it is held to and one example that passes them
before it writes its own first Step.
"""
import os
import re

import pytest

from core.lint import lint
from core.prompts import ROOT, read_prompt

EXECUTE = read_prompt("execute.md")

LINT_PROMPT_DIR = os.path.join(ROOT, "lint")

LINT_PROMPT_FILES = {os.path.splitext(name)[0]
                     for name in os.listdir(LINT_PROMPT_DIR) if name.endswith(".md")}

CONSTRAINT_CHECKLIST_LINE = (
    "A `Sketcher.Constraint` call must name a real constraint type with the arity "
    "that type takes"
)

# Each file in config/prompts/lint/ maps to the offending code that triggers it,
# the fragment of the lint's own reason, and the checklist line that states it --
# the two constraint files share one line, since a wrong arity and an unknown
# name are one rule read together.
RULES = {
    "lint_dimensional_literal": (
        "pad.Length = 80.0\n", "bare numeral",
        "No bare numeral in a dimensional position"),
    "lint_dimensional_default": (
        "pad.Length = design['features']['Boss'].get('length', 80.0)\n",
        "resolved from `design` with `.get",
        "No `.get(key, default)` on a Design field"),
    "lint_element": (
        "fillet.Base = (box, ['Edge12'])\n", "literal element name",
        "No literal element name"),
    "lint_gui": (
        "Gui.ActiveDocument\n", "not available during Preflight",
        "No `Gui.*` or reading the live selection"),
    "lint_swallowed": (
        "try:\n    pad.Length = 1\nexcept:\n    pass\n", "bare `except`",
        "No bare `except`"),
    "lint_allocated_name": (
        "doc.addObject('PartDesign::Pad', 'Boss')\ndoc.getObject('Boss')\n",
        "was just created by this Step's own",
        "No lookup of an object this Step just allocated"),
    "lint_uncounted_selection": (
        "edges = get_edges()\nfillet.Base = (box, edges)\n",
        "nothing says how many elements it expected to find",
        "No selection assigned to `.Base` whose length nothing checked"),
    "lint_boundbox_guard": (
        "assert shape.BoundBox.XMax > 10\n", "tests a guard against Shape.BoundBox",
        "No guard comparing a `Shape.BoundBox` attribute"),
    "lint_raw_shape": (
        "pad = doc.addObject('PartDesign::Pad', 'Boss')\npad.Shape = box.Shape\n",
        "came from PartDesign",
        "No `.Shape =` assignment or `addProperty` on a PartDesign feature"),
    "lint_attachment_support_empty_list": (
        "sketch.AttachmentSupport = [(plane, [])]\n", "empty list literal",
        "No empty list literal as `AttachmentSupport`'s subelement"),
    "lint_activation": (
        "x = body.ActiveObject\n", "makes an object active",
        "No making an object active"),
    "lint_constraint_arity": (
        "sketch.addConstraint(Sketcher.Constraint('Coincident', a, 3, b))\n",
        "was given", CONSTRAINT_CHECKLIST_LINE),
    "lint_unknown_constraint": (
        "sketch.addConstraint(Sketcher.Constraint('Concentric', a, b))\n",
        "is not a Sketcher constraint type", CONSTRAINT_CHECKLIST_LINE),
}


def test_every_lint_prompt_file_has_a_rule_in_this_test():
    """The repo forbids a hardcoded list nothing checks for completeness."""
    assert set(RULES) == LINT_PROMPT_FILES


def _worked_example():
    blocks = re.findall(r"```python\n(.*?)```", EXECUTE, re.DOTALL)
    assert len(blocks) == 1, "execute.md should carry exactly one worked Step example"
    return blocks[0]


def _checklist_lines():
    match = re.search(r"^## Checklist\n(.*?)^## ", EXECUTE, re.DOTALL | re.MULTILINE)
    assert match, "execute.md has no ## Checklist section"
    return [line for line in match.group(1).splitlines() if line.startswith("- ")]


def test_the_checklist_header_names_no_count():
    match = re.search(r"^## Checklist\n\n(.*)", EXECUTE, re.MULTILINE)
    assert match
    header = match.group(1).splitlines()[0]
    assert not re.search(r"\d", header), \
        "the checklist header names a count, which drifts: %r" % header


def test_the_checklist_has_one_line_per_lint_file_once_merged():
    lines = _checklist_lines()
    distinct_rules = {checklist_line for _code, _reason, checklist_line in RULES.values()}
    assert len(lines) == len(distinct_rules)


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


@pytest.mark.parametrize("rule, code, reason_fragment, checklist_line", [
    (rule, code, reason_fragment, checklist_line)
    for rule, (code, reason_fragment, checklist_line) in RULES.items()
])
def test_every_checklist_line_matches_a_refusal_the_lint_makes(
        rule, code, reason_fragment, checklist_line):
    result = lint(code)

    assert not result.ok, "expected the lint to refuse: %s" % rule
    assert reason_fragment in result.report()
    assert any(checklist_line in line for line in _checklist_lines()), \
        "no checklist line reads: %s" % checklist_line
