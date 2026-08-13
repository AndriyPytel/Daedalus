"""
The AST lint that runs before Preflight.

Five rules, each from an ADR, each about a failure a Preflight traceback
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
- **Nothing may be made active** (ADR-0008). The active Body lives in the
  GUI: `App.Document` has no active-object API in any spelling, so no spelling
  of it works headless and the traceback has no right answer to point at.
  Each attempt is a bare `AttributeError` the assistant answers by trying the
  next spelling, and three of them spend the whole Step budget.
- **No raw shape passed off as a PartDesign feature** (ADR-0012). Assigning
  `.Shape` on a `PartDesign::*` object, or bolting fields onto one with
  `addProperty`, builds a feature tree that is fiction: the sketch is
  decorative, nothing rebuilds when a dimension changes, and no traceback
  appears anywhere — Preflight recomputes clean and Review sees a correct
  picture. `Part::Feature` is untouched; the rule is about the disguise. The
  reason names the feature types rather than asking for "a real feature",
  because every prohibition short of that is answered with the next spelling
  of the same workaround.
- **No Step may swallow its own failure** (ADR-0008). A bare `except`, or a
  handler that only passes, discards the traceback the correction path is
  built on. Code that catches its own exception and says nothing disables the
  mechanism the architecture rests on.

A Sketcher constraint is checked against a hardcoded table of names and
arities. `Sketcher.Constraint`'s C++ constructor segfaults on a name it does
not know and on a known name at the wrong arity — no traceback, nothing to
correct against. The arities are the argument counts *after* the name, read off
the PyArg_ParseTuple ladder in FreeCAD 1.0's
src/Mod/Sketcher/App/ConstraintPyImp.cpp; Sketcher exposes no introspection for
signatures, and this module is tested without FreeCAD, so the table is
hardcoded on purpose. `InternalAlignment` is matched by substring in the C++,
and spelled "InternalAlignment:Foo". Every signature also takes an optional
trailing `activated`, and a dimensional one an optional `driving` after it;
whether an argument is a bool is not something the AST can tell, so the two
extra positions are simply allowed.

Element names the *user* selected are explicit input, not a guess, so they are
allowed through when they match the live selection. Only Edge, Face and Vertex
are matched: Wire, Shell and Solid belong to an earlier version of this pattern
that rejected "Solid1" as an object Label.

This module replaces the old validator's hand-maintained substring rules. It
does not port them: a rule per surprise, forever, is what introspection and
Preflight tracebacks are for.
"""
import ast
import re
from dataclasses import dataclass, field
from typing import List

ELEMENT = re.compile(r"^(Edge|Face|Vertex)\d+$")

GUI_NAMES = ("Gui", "FreeCADGui")

ACTIVATION_NAMES = (
    "ActiveObject", "setActiveObject", "getActiveObject", "activeView", "ActiveView",
)

_CONSTRAINT_ARITIES = {
    "Horizontal":    {1, 4},
    "Vertical":      {1, 4},
    "Block":         {1},
    "Parallel":      {2},
    "Equal":         {2},
    "Radius":        {2},
    "Diameter":      {2},
    "Weight":        {2},
    "Tangent":       {2, 3, 4},
    "Perpendicular": {2, 3, 4},
    "PointOnObject": {3},
    "Coincident":    {4},
    "TangentViaPoint": {4},
    "Distance":      {2, 3, 4, 5},
    "Angle":         {2, 3, 5},
    "DistanceX":     {2, 3, 5},
    "DistanceY":     {2, 3, 5},
    "Symmetric":     {5, 6},
    "SnellsLaw":     {6},
    "InternalAlignment": {2, 3},
}

_CONSTRAINT_SIGNATURES = {
    name: {arity + extra for arity in arities for extra in (0, 1, 2)}
    for name, arities in _CONSTRAINT_ARITIES.items()
}

_UNKNOWN_CONSTRAINT_REASON = (
    "%r is not a Sketcher constraint type. The C++ constructor does not raise "
    "for a name it does not know — it takes FreeCAD down, so there is no "
    "traceback to correct against. The types are: %s. Concentricity in "
    "particular is not one of them: two circles are concentric because they "
    "were given the same centre, and where it must genuinely be a driven "
    "parameter it is Coincident between the two centres — "
    "Constraint('Coincident', a, 3, b, 3), where 3 is the centre PosId."
)

_CONSTRAINT_ARITY_REASON = (
    "Constraint(%r, ...) was given %d arguments after the name, which is not a "
    "signature this constructor has — and a wrong arity crashes the core rather "
    "than raising. %r takes %s arguments after the name. For concentricity "
    "between two circles that is Constraint('Coincident', a, 3, b, 3), where 3 "
    "is the centre PosId."
)

_ELEMENT_REASON = (
    "%r is a literal element name. It points somewhere else as soon as a "
    "parameter upstream in the history changes, and nothing downstream can "
    "detect that the fillet landed on the wrong edge. Compute the reference at "
    "execution time instead, or use an element the user has actually selected. "
    "Better still, pick an operation that needs no element name at all — a "
    "PartDesign::Hole rather than a bore you then have to go and find."
)

_RAW_SHAPE_REASON = (
    "%s came from PartDesign and a PartDesign feature computes its own Shape "
    "from its Profile — assigning it, or bolting fields on with addProperty, "
    "produces a feature tree that is fiction. Nothing raises: the sketch is "
    "decorative, Preflight recomputes clean, and the part stops rebuilding "
    "when a dimension changes. Use the feature that performs the operation — "
    "PartDesign::Pad, PartDesign::Pocket, PartDesign::Revolution, "
    "PartDesign::Groove, PartDesign::AdditiveHelix, PartDesign::Hole, "
    "PartDesign::Fillet, PartDesign::Chamfer — on a real sketch. If the shape "
    "genuinely belongs outside a Body, build it as Part::Feature and say so."
)

_SWALLOWED_REASON = (
    "This Step's error handling breaks the correction path: %s. A failed Step "
    "answers itself (ADR-0008) — the traceback is what goes back and gets "
    "corrected, so code that swallows its own exception disables the mechanism "
    "the whole run rests on. Let it raise. A Step that applied none of the "
    "twelve edges it meant to must fail, not record how many it managed."
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

    The GUI is caught as an attribute as well as a name, so that
    `App.Gui.Selection`, `FreeCAD.Gui` and `doc.Gui` are caught too: reaching
    the GUI through something else is the same problem by a longer route.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return LintResult(["The code does not parse: %s (line %s)" % (exc.msg, exc.lineno)])

    allowed = selected_elements(selection)
    features = _partdesign_features(tree)
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

        elif isinstance(node, ast.Attribute) and node.attr in GUI_NAMES:
            complain(_GUI_REASON % node.attr)

        elif isinstance(node, ast.Attribute) and node.attr in ACTIVATION_NAMES:
            complain(_ACTIVATION_REASON % node.attr)

        elif isinstance(node, ast.ExceptHandler):
            swallowed = _swallowed(node)
            if swallowed:
                complain(_SWALLOWED_REASON % swallowed)

        elif isinstance(node, ast.Assign):
            disguised = _shape_assignment(node, features)
            if disguised:
                complain(_RAW_SHAPE_REASON % disguised)

        elif isinstance(node, ast.Call):
            disguised = _added_property(node, features)
            if disguised:
                complain(_RAW_SHAPE_REASON % disguised)
            elif _is_sketcher_constraint(node):
                problem = _constraint_problem(node)
                if problem:
                    complain(problem)

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


def _partdesign_features(tree) -> set:
    """
    Variables holding an object this Step made with a `PartDesign::` type.

    Only what the AST can see: `x = <anything>.addObject('PartDesign::…', …)`
    and the `newObject` spelling a Body takes. A feature fetched from the
    Document by name is not tracked — the type is not in the source to read.
    """
    features = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not (isinstance(call.func, ast.Attribute)
                and call.func.attr in ("addObject", "newObject") and call.args):
            continue
        first = call.args[0]
        if not (isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and first.value.startswith("PartDesign::")):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                features.add(target.id)
    return features


def _shape_assignment(node: ast.Assign, features: set):
    """The name in `feature.Shape = ...`, where the feature came from PartDesign."""
    for target in node.targets:
        if (isinstance(target, ast.Attribute) and target.attr == "Shape"
                and isinstance(target.value, ast.Name)
                and target.value.id in features):
            return target.value.id
    return None


def _added_property(node: ast.Call, features: set):
    """The name in `feature.addProperty(...)`, same tracking."""
    if (isinstance(node.func, ast.Attribute) and node.func.attr == "addProperty"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in features):
        return node.func.value.id
    return None


def _swallowed(handler: ast.ExceptHandler):
    """
    Why this handler discards the failure, or None.

    Two shapes and no more: a bare `except`, which catches what it was never
    written for, and a body that does nothing with what it caught. A handler
    that takes a different route on purpose — a fallback import, a default —
    is left alone; refusing those would be the rule per surprise this module
    exists without.
    """
    if handler.type is None:
        return "a bare `except` catches everything, including what you did not foresee"
    if all(isinstance(statement, (ast.Pass, ast.Continue))
           or (isinstance(statement, ast.Expr)
               and isinstance(statement.value, ast.Constant)
               and statement.value.value is Ellipsis)
           for statement in handler.body):
        return "the handler does nothing with the exception it caught"
    return None


def _is_sketcher_constraint(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr == "Constraint"


def _constraint_problem(node: ast.Call):
    """
    The reason this Constraint call would crash the core, or None.

    Only a call that can be read statically is judged: a literal name, no
    starred argument, no keyword. Anything else is not this rule's business.
    """
    if node.keywords or not node.args:
        return None
    if any(isinstance(argument, ast.Starred) for argument in node.args):
        return None

    name = node.args[0]
    if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
        return None

    known = _CONSTRAINT_SIGNATURES.get(name.value.split(":")[0])
    if known is None:
        return _UNKNOWN_CONSTRAINT_REASON % (
            name.value, ", ".join(sorted(_CONSTRAINT_ARITIES)))

    arity = len(node.args) - 1
    if arity in known:
        return None
    allowed = sorted(_CONSTRAINT_ARITIES[name.value.split(":")[0]])
    return _CONSTRAINT_ARITY_REASON % (
        name.value, arity, name.value,
        " or ".join(str(count) for count in allowed))


def _element_names(text: str):
    """
    Element names inside a string constant.

    Catches the bare "Edge12" and the qualified "Box.Edge12", which is how
    these actually get written.
    """
    for part in text.split("."):
        if ELEMENT.match(part):
            yield part
