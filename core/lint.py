"""
The AST lint that runs before Preflight.

Three rules, each from an ADR, each about a failure a Preflight traceback
cannot teach the way out of (ADR-0008):

- **No literal `Edge12` / `Face3` / `Vertex7`** (ADR-0005). FreeCAD 1.0's
  topological naming mitigation made these less fragile, not stable. A
  hardcoded element name does not crash — the fillet is applied to the wrong
  edge and the model silently deforms. A silent wrong answer is worse than a
  traceback, because Preflight cannot see it.
- **No `Gui.*` and no live selection** (ADR-0002). Generated code must be a
  pure function of the Document, or Preflight and Apply can disagree — the
  worker has no GUI and no selection, so anything reading them means the two
  processes are running different programs.
- **Nothing may be made active** (ADR-0008). Activation is GUI-only, so no
  spelling of it works headless and the traceback has no right answer to
  point at — introspection would only confirm the absence, once per guess.

Element names the *user* selected are explicit input, not a guess, so they are
allowed through when they match the live selection.

This module replaces the old validator's hand-maintained substring rules. It
does not port them: a rule per surprise, forever, is what introspection and
Preflight tracebacks are for.
"""
import ast
import re
from dataclasses import dataclass, field
from typing import List

# ADR-0005 names these three and no others. Wire/Shell/Solid were in an earlier
# version of this pattern and rejected "Solid1" as an object Label.
ELEMENT = re.compile(r"^(Edge|Face|Vertex)\d+$")

GUI_NAMES = ("Gui", "FreeCADGui")

# The active Body lives in the GUI. App.Document has no active-object API in
# any spelling, so each attempt is a bare AttributeError the assistant answers
# by trying the next spelling — three of them spend the whole Step budget.
ACTIVATION_NAMES = (
    "ActiveObject", "setActiveObject", "getActiveObject", "activeView", "ActiveView",
)

_ELEMENT_REASON = (
    "%r is a literal element name. It points somewhere else as soon as a "
    "parameter upstream in the history changes, and nothing downstream can "
    "detect that the fillet landed on the wrong edge. Compute the reference at "
    "execution time instead — find the edge by its geometry (length, direction, "
    "position, adjacency) from doc.getObject(...).Shape — or use an element the "
    "user has actually selected."
)

_GUI_REASON = (
    "%s is not available during Preflight. The worker is a headless FreeCAD "
    "with no GUI and no selection, so code that reads them runs differently "
    "there than it will here, and Preflight stops meaning anything. Make the "
    "Step a pure function of the Document."
)


_ACTIVATION_REASON = (
    "%s makes an object active, which only the GUI can do. The Preflight "
    "Document has no active object and no active view, so there is no spelling "
    "of this that works. Nothing needs to be active: put a PartDesign feature "
    "in its Body with Body.addObject(feature) instead."
)


@dataclass
class LintResult:
    problems: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def __bool__(self):
        return self.ok

    def report(self) -> str:
        if self.ok:
            return ""
        return "The Step was rejected before Preflight:\n\n" + "\n\n".join(
            "- " + problem for problem in self.problems
        )


def selected_elements(selection) -> set:
    """
    Flatten a Snapshot's selection into the element names it makes legitimate.

    Accepts what `snapshot` returns: [{"object": "Box", "elements": ["Edge3"]}].
    """
    allowed = set()
    for entry in selection or ():
        for element in entry.get("elements", ()):
            allowed.add(element)
            allowed.add("%s.%s" % (entry.get("object"), element))
    return allowed


def lint(code: str, selection=None) -> LintResult:
    """
    Check *code* before it costs a Preflight.

    `selection` is the Snapshot's selection; element names in it are treated as
    explicit user input and allowed.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return LintResult(["The code does not parse: %s (line %s)" % (exc.msg, exc.lineno)])

    allowed = selected_elements(selection)
    problems = []
    seen = set()

    def complain(message):
        if message not in seen:
            seen.add(message)
            problems.append(message)

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for part in _element_names(node.value):
                if part not in allowed and node.value not in allowed:
                    complain(_ELEMENT_REASON % node.value)

        elif isinstance(node, ast.Name) and node.id in GUI_NAMES:
            complain(_GUI_REASON % node.id)

        # Also catches App.Gui.Selection..., FreeCAD.Gui..., doc.Gui... — reaching
        # the GUI through something else is the same problem by a longer route.
        elif isinstance(node, ast.Attribute) and node.attr in GUI_NAMES:
            complain(_GUI_REASON % node.attr)

        elif isinstance(node, ast.Attribute) and node.attr in ACTIVATION_NAMES:
            complain(_ACTIVATION_REASON % node.attr)

        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in GUI_NAMES:
                    complain(_GUI_REASON % alias.name)

        elif isinstance(node, ast.ImportFrom):
            if node.module in GUI_NAMES:
                complain(_GUI_REASON % node.module)
            for alias in node.names:
                if alias.name in GUI_NAMES:
                    complain(_GUI_REASON % alias.name)

    return LintResult(problems)


def _element_names(text: str):
    """
    Element names inside a string constant.

    Catches the bare "Edge12" and the qualified "Box.Edge12", which is how
    these actually get written.
    """
    for part in text.split("."):
        if ELEMENT.match(part):
            yield part
