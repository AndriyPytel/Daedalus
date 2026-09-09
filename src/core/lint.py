"""The AST lint that runs before Preflight — rules about silent-wrong-answer failures a traceback
can't teach a way out of.

Constraints:
No literal Edge12/Face3/Vertex7 (ADR-0005) — topological naming makes them silently wrong.
No Gui.*/live selection (ADR-0002) — Preflight and Apply run headless.
Nothing may be made active (ADR-0008) — no headless active-object API exists.
No raw .Shape/addProperty disguised as a PartDesign feature (ADR-0012).
A computed selection (list/comprehension Base) must assert its own length.
No bare except/pass swallows a Step's failure (ADR-0008).
No literal-name lookup of an object this Step just allocated — a collision renames it silently.
"""
import ast
import re
from dataclasses import dataclass, field
from typing import List

from core.prompts import read_prompt

ELEMENT = re.compile(r"^(Edge|Face|Vertex)\d+$")

GUI_NAMES = ("Gui", "FreeCADGui")

_PRIMITIVE_SHAPES = ("Box", "Cylinder", "Sphere", "Cone", "Ellipsoid", "Torus", "Prism", "Wedge")

OP_TYPEIDS = {
    "pad": ("PartDesign::Pad",),
    "revolve": ("PartDesign::Revolution",),
    "additive-loft": ("PartDesign::AdditiveLoft",),
    "additive-sweep": ("PartDesign::AdditivePipe",),
    "additive-helix": ("PartDesign::AdditiveHelix",),
    "additive-primitive": tuple("PartDesign::Additive%s" % shape for shape in _PRIMITIVE_SHAPES),
    "pocket": ("PartDesign::Pocket",),
    "hole": ("PartDesign::Hole", "PartDesign::Pocket"),
    "groove": ("PartDesign::Groove",),
    "subtractive-loft": ("PartDesign::SubtractiveLoft",),
    "subtractive-sweep": ("PartDesign::SubtractivePipe",),
    "subtractive-helix": ("PartDesign::SubtractiveHelix",),
    "subtractive-primitive": tuple("PartDesign::Subtractive%s" % shape for shape in _PRIMITIVE_SHAPES),
    "fillet": ("PartDesign::Fillet",),
    "chamfer": ("PartDesign::Chamfer",),
    "draft": ("PartDesign::Draft",),
    "thickness": ("PartDesign::Thickness",),
    "mirror": ("PartDesign::Mirrored",),
    "linear-pattern": ("PartDesign::LinearPattern",),
    "polar-pattern": ("PartDesign::PolarPattern",),
    "multi-transform": ("PartDesign::MultiTransform",),
    "scale": ("PartDesign::Scaled",),
    "sketch": ("Sketcher::SketchObject",),
    "datum-plane": ("PartDesign::Plane",),
    "datum-line": ("PartDesign::Line",),
    "datum-point": ("PartDesign::Point",),
    "local-coordinate-system": ("PartDesign::CoordinateSystem",),
    "sub-shape-binder": ("PartDesign::SubShapeBinder", "PartDesign::ShapeBinder"),
    "body": ("PartDesign::Body",),
    "fuse": ("Part::MultiFuse", "Part::Fuse"),
    "cut": ("Part::Cut",),
    "common": ("Part::Common", "Part::MultiCommon"),
    "thread": ("PartDesign::Hole",),
    "extend": (),
    "trim": (),
    "offset": ("Part::Offset", "Part::Offset2D"),
}

_SCAFFOLDING_OPS = ("sketch", "datum-plane", "datum-line", "datum-point",
                    "local-coordinate-system", "sub-shape-binder", "body")

_SCAFFOLD_TYPEIDS = frozenset(
    typeid for op in _SCAFFOLDING_OPS for typeid in OP_TYPEIDS[op]
)


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

_UNKNOWN_CONSTRAINT_REASON = read_prompt("lint_unknown_constraint.md")
_CONSTRAINT_ARITY_REASON = read_prompt("lint_constraint_arity.md")
_ELEMENT_REASON = read_prompt("lint_element.md")
_RAW_SHAPE_REASON = read_prompt("lint_raw_shape.md")
_SWALLOWED_REASON = read_prompt("lint_swallowed.md")
_UNCOUNTED_SELECTION_REASON = read_prompt("lint_uncounted_selection.md")
_GUI_REASON = read_prompt("lint_gui.md")

_TYPEID_REASON = (
    "addObject(%r, ...) does not build %r. ADR-0026's verb-to-TypeId table says "
    "%r builds %s, and no other TypeId is this Step's operation."
)

_ACTIVATION_REASON = read_prompt("lint_activation.md")


_ALLOCATED_NAME_REASON = read_prompt("lint_allocated_name.md")


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


def lint(code: str, selection=None, op=None) -> LintResult:
    """
    Check *code* before it costs a Preflight.

    `selection` is the Snapshot's selection; element names in it are allowed.
    `op` is the Step's declared operation (ADR-0026); given one, every `addObject`
    building a `PartDesign::`/`Part::` type is checked against `OP_TYPEIDS`.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return LintResult(["The code does not parse: %s (line %s)" % (exc.msg, exc.lineno)])

    allowed = selected_elements(selection)
    features = _partdesign_features(tree)
    counted = _counted_selections(tree)
    allocated = _names_allocated_here(tree)
    allowed_typeids = set(OP_TYPEIDS.get(op, ())) | _SCAFFOLD_TYPEIDS if op in OP_TYPEIDS else None
    problems = []
    seen = set()

    def complain(message):
        if message not in seen:
            seen.add(message)
            problems.append(message)

    def _on_constant(node):
        if not isinstance(node.value, str):
            return
        for part in _element_names(node.value):
            if part not in allowed and node.value not in allowed:
                complain(_ELEMENT_REASON % node.value)

    def _on_name(node):
        if node.id in GUI_NAMES:
            complain(_GUI_REASON % node.id)

    def _on_attribute(node):
        if node.attr in GUI_NAMES:
            complain(_GUI_REASON % node.attr)
        elif node.attr in ACTIVATION_NAMES:
            complain(_ACTIVATION_REASON % node.attr)

    def _on_except_handler(node):
        swallowed = _swallowed(node)
        if swallowed:
            complain(_SWALLOWED_REASON % swallowed)

    def _on_assign(node):
        disguised = _shape_assignment(node, features)
        if disguised:
            complain(_RAW_SHAPE_REASON % disguised)
        uncounted = _uncounted_selection(node, counted)
        if uncounted:
            complain(_UNCOUNTED_SELECTION_REASON % uncounted)

    def _on_call(node):
        disguised = _added_property(node, features)
        if disguised:
            complain(_RAW_SHAPE_REASON % disguised)
        elif _is_sketcher_constraint(node):
            problem = _constraint_problem(node)
            if problem:
                complain(problem)
        else:
            mismatch = _typeid_mismatch(node, allowed_typeids)
            if mismatch:
                complain(_TYPEID_REASON % (mismatch, mismatch, op, " or ".join(OP_TYPEIDS[op])))
            reused = _allocated_name_lookup(node, allocated)
            if reused:
                complain(_ALLOCATED_NAME_REASON % (reused, reused))

    def _on_import(node):
        for alias in node.names:
            if alias.name in GUI_NAMES:
                complain(_GUI_REASON % alias.name)

    def _on_import_from(node):
        if node.module in GUI_NAMES:
            complain(_GUI_REASON % node.module)
        for alias in node.names:
            if alias.name in GUI_NAMES:
                complain(_GUI_REASON % alias.name)

    node_handlers = {
        ast.Constant: _on_constant,
        ast.Name: _on_name,
        ast.Attribute: _on_attribute,
        ast.ExceptHandler: _on_except_handler,
        ast.Assign: _on_assign,
        ast.Call: _on_call,
        ast.Import: _on_import,
        ast.ImportFrom: _on_import_from,
    }

    for node in ast.walk(tree):
        handler = node_handlers.get(type(node))
        if handler is not None:
            handler(node)

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


def _names_allocated_here(tree) -> set:
    """Literal names this Step's own code handed to addObject/newObject."""
    allocated = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("addObject", "newObject") and len(node.args) >= 2):
            continue
        name = node.args[1]
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            allocated.add(name.value)
    return allocated


def _allocated_name_lookup(node: ast.Call, allocated: set):
    """The literal name in `doc.getObject('Name')`, where 'Name' is one this
    Step's own code just allocated -- or None."""
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == "getObject"
            and node.args):
        return None
    name = node.args[0]
    if isinstance(name, ast.Constant) and isinstance(name.value, str) and name.value in allocated:
        return name.value
    return None


def _typeid_mismatch(node, allowed_typeids):
    """
    The TypeId an `addObject`/`newObject` call builds, given only where
    `allowed_typeids` is not `None` (an `op` on the closed 36-verb list was
    given) and the type is one this ticket's table has an opinion about —
    `PartDesign::` or `Part::`. Anything else (`App::` groups, spreadsheets)
    is out of scope for a verb list about modelling operations.
    """
    if allowed_typeids is None or not (isinstance(node.func, ast.Attribute)
                                       and node.func.attr in ("addObject", "newObject")
                                       and node.args):
        return None
    first = node.args[0]
    if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
        return None
    type_id = first.value
    if not type_id.startswith(("PartDesign::", "Part::")):
        return None
    if type_id in allowed_typeids:
        return None
    return type_id


def _counted_selections(tree) -> set:
    """
    Variables whose length this Step checks against a number, and raises on.

    An `if` that ends in a `raise` and an `assert` are the two shapes; a
    length compared to nothing in particular — `if not edges` — is not one of
    them, because an empty selection is only the extreme of the failure. The
    recorded run's fillet had exactly that guard, found eight of the edges its
    Step described, and passed it.
    """
    counted = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            test = node.test
        elif isinstance(node, ast.If) and any(isinstance(inner, ast.Raise)
                                              for inner in ast.walk(node)):
            test = node.test
        else:
            continue
        if not any(isinstance(inner, ast.Constant) and isinstance(inner.value, int)
                   for inner in ast.walk(test)):
            continue
        for inner in ast.walk(test):
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                    and inner.func.id == "len" and len(inner.args) == 1
                    and isinstance(inner.args[0], ast.Name)):
                counted.add(inner.args[0].id)
    return counted


def _uncounted_selection(node: ast.Assign, counted: set):
    """
    The `x.Base = (obj, elements)` whose element list nothing counted, or None.

    A list written out in the code states its own count; an uncounted variable
    states nothing, and that is the whole of the failure — the filter runs,
    finds what it finds, and the Step applies to that.

    A comprehension inherits the count of what it walks. Walking a list the
    Step counted and raised on, and dropping none of it, yields one name per
    element of a length that has already been checked; an `if` in the
    comprehension, or a source nothing counted, states nothing again.
    """
    for target in node.targets:
        if not (isinstance(target, ast.Attribute) and target.attr == "Base"):
            continue
        value = node.value
        if not (isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == 2):
            continue
        elements = value.elts[1]
        if isinstance(elements, ast.Name) and elements.id not in counted:
            return ast.unparse(target)
        if isinstance(elements, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
            if not all(isinstance(gen.iter, ast.Name) and gen.iter.id in counted
                       and not gen.ifs for gen in elements.generators):
                return ast.unparse(target)
    return None


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
