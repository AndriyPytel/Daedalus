"""
The AST lint that runs before Preflight: rules about silent-wrong-answer
failures a traceback can't teach a way out of. No literal Edge12/Face3/Vertex7,
since topological naming makes them silently wrong; no Gui.*/live selection,
because Preflight and Apply run headless; nothing may be made active, since
no headless active-object API exists; no raw .Shape/addProperty disguised as
a PartDesign feature; no empty-list AttachmentSupport subelement, since it
attaches nothing without raising; no guard comparing a BoundBox attribute,
since a BoundBox lands inside the true extreme it claims to report. A
computed selection must assert its own length, no bare except/pass swallows a
Step's failure, and no literal-name lookup of an object this Step just
allocated, since a collision would rename it silently -- `dimensional_literal`
and `dimensional_default` are wired into `lint()`'s refusal list now that
`design` is in a Step's own namespace: a bare numeral in a dimensional
position is refused, and so is a Design reference resolved with
`.get(key, default)` instead of a plain index, since a field Design left
`not_defined` must raise rather than resolve quietly.
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

_ATTACHMENT_SUPPORT_REASON = read_prompt("lint_attachment_support_empty_list.md")
_BOUNDBOX_GUARD_REASON = read_prompt("lint_boundbox_guard.md")
_DIMENSIONAL_LITERAL_REASON = read_prompt("lint_dimensional_literal.md")
_DIMENSIONAL_DEFAULT_REASON = read_prompt("lint_dimensional_default.md")

DIMENSION_ATTRS = frozenset((
    "Length", "Length2", "Radius", "Diameter", "HoleCutDiameter",
    "HoleCutDepth", "Angle", "TaperAngle", "Growth", "Size", "Pitch",
))

_NOT_A_DIMENSION = frozenset((0, 1, 90, 360))

_BOUNDBOX_ATTRS = frozenset((
    "XMin", "XMax", "YMin", "YMax", "ZMin", "ZMax",
    "Center", "XLength", "YLength", "ZLength",
))


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
    Check *code* before it is replayed onto the Document. `selection` is the
    Snapshot's selection; element names in it are allowed. Whether the Step
    built the TypeId its declared `op` promises is answered by
    `op_typeid_mismatch`, off what the kernel actually built rather than by
    parsing this string.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return LintResult(["The code does not parse: %s (line %s)" % (exc.msg, exc.lineno)])

    allowed = selected_elements(selection)
    features = _partdesign_features(tree)
    counted = _counted_selections(tree)
    allocated = _names_allocated_here(tree)
    boundbox_names = _boundbox_names(tree)
    bindings = literal_bindings(tree)
    problems = []
    seen = set()

    def complain(message):
        if message not in seen:
            seen.add(message)
            problems.append(message)

    for test in _guard_tests(tree):
        guarded = _boundbox_guard(test, boundbox_names)
        if guarded:
            complain(_BOUNDBOX_GUARD_REASON % guarded)

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
        empty_attachment = _attachment_support_empty_list(node)
        if empty_attachment:
            complain(_ATTACHMENT_SUPPORT_REASON % empty_attachment)
        literal = dimensional_literal(node, bindings)
        if literal:
            complain(_DIMENSIONAL_LITERAL_REASON % literal)
        defaulted = dimensional_default(node)
        if defaulted:
            complain(_DIMENSIONAL_DEFAULT_REASON % defaulted)

    def _on_call(node):
        disguised = _added_property(node, features)
        if disguised:
            complain(_RAW_SHAPE_REASON % disguised)
        elif _is_sketcher_constraint(node):
            problem = _constraint_problem(node)
            if problem:
                complain(problem)
        else:
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


def op_typeid_mismatch(op, created, objects):
    """
    Whether a Step's declared `op` does not match what it built, read off
    the kernel's own report of its checkpoint's objects rather than parsed
    out of the code, since the code already ran and what it built is a fact
    to look up, not a guess. `objects` is the kernel's
    `[{"name", "label", "type"}, ...]`; `created` is the names among them
    this Step made. Only `PartDesign::`/`Part::` types are checked, `App::`
    is out of scope, and `None` comes back where `op` is off the closed
    36-verb list.
    """
    if op not in OP_TYPEIDS:
        return None
    allowed = set(OP_TYPEIDS[op]) | _SCAFFOLD_TYPEIDS
    built = {entry.get("name"): entry.get("type") for entry in objects or ()}
    for name in created or ():
        type_id = built.get(name)
        if not type_id or not type_id.startswith(("PartDesign::", "Part::")):
            continue
        if type_id not in allowed:
            return type_id
    return None


def _guard_tests(tree):
    """
    The test of every guard a Step can fail on: an `if` that ends in a
    `raise`, and an `assert`, and no other shape. `_counted_selections` and
    the BoundBox rule both read a guard's test for what it checks, not
    whether it fires.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            yield node.test
        elif isinstance(node, ast.If) and any(isinstance(inner, ast.Raise)
                                              for inner in ast.walk(node)):
            yield node.test


def _counted_selections(tree) -> set:
    """
    Variables whose length this Step checks against a number, and raises on:
    an `if` that ends in a `raise`, and an `assert`, are the two shapes, and
    a length compared to nothing in particular -- `if not edges` -- is not
    one of them, since an empty selection is only the extreme of the
    failure. The recorded run's fillet had exactly that guard, found eight
    of the edges its Step described, and passed it. A name assigned from a
    comprehension over a counted source, dropping nothing, is itself
    counted, and assignments run in a fixpoint so a chain of such names
    carries the count too.
    """
    counted = set()
    for test in _guard_tests(tree):
        if not any(isinstance(inner, ast.Constant) and isinstance(inner.value, int)
                   for inner in ast.walk(test)):
            continue
        for inner in ast.walk(test):
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                    and inner.func.id == "len" and len(inner.args) == 1
                    and isinstance(inner.args[0], ast.Name)):
                counted.add(inner.args[0].id)

    comprehension_assigns = [node for node in ast.walk(tree)
                              if isinstance(node, ast.Assign)
                              and len(node.targets) == 1
                              and isinstance(node.targets[0], ast.Name)
                              and isinstance(node.value, (ast.ListComp, ast.GeneratorExp, ast.SetComp))]
    grown = True
    while grown:
        grown = False
        for node in comprehension_assigns:
            name = node.targets[0].id
            if name in counted:
                continue
            if all(isinstance(gen.iter, ast.Name) and gen.iter.id in counted
                   and not gen.ifs for gen in node.value.generators):
                counted.add(name)
                grown = True
    return counted


def _boundbox_names(tree) -> set:
    """Variables assigned straight from `<geometry>.BoundBox`, so `bb.ZMax`
    is read as the same reference as `shape.BoundBox.ZMax`."""
    names = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Attribute) and node.value.attr == "BoundBox"):
            names.add(node.targets[0].id)
    return names


def _is_boundbox_reference(node, boundbox_names: set) -> bool:
    if isinstance(node, ast.Attribute) and node.attr == "BoundBox":
        return True
    if isinstance(node, ast.Name) and node.id in boundbox_names:
        return True
    if isinstance(node, ast.Attribute) and node.attr in _BOUNDBOX_ATTRS:
        value = node.value
        if isinstance(value, ast.Attribute) and value.attr == "BoundBox":
            return True
        if isinstance(value, ast.Name) and value.id in boundbox_names:
            return True
    return False


def _boundbox_guard(test, boundbox_names: set):
    """
    The offending expression in a guard's test that reads a BoundBox
    attribute inside a comparison, or None. Printing a BoundBox, or reading
    one into a name nothing asserts or raises on, never reaches here --
    only a `Compare` inside an assert's test or a raising `if`'s test does.
    """
    for node in ast.walk(test):
        if not isinstance(node, ast.Compare):
            continue
        for inner in ast.walk(node):
            if _is_boundbox_reference(inner, boundbox_names):
                return ast.unparse(node)
    return None


def _uncounted_selection(node: ast.Assign, counted: set):
    """
    The `x.Base = (obj, elements)` whose element list nothing counted, or
    None. A list written out in the code states its own count; an uncounted
    variable states nothing, and that is the whole of the failure -- the
    filter runs, finds what it finds, and the Step applies to that. A
    comprehension inherits the count of what it walks, dropping none of it
    to yield one name per element of an already-checked length, while an
    `if` in the comprehension, or a source nothing counted, states nothing
    again.
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


def _attachment_support_empty_list(node: ast.Assign):
    """
    The unparsed assignment where `.AttachmentSupport` is given an empty
    list literal as a subelement, or None. FreeCAD 1.1.3 raises nothing for
    `AttachmentSupport = [(plane, [])]`: MapMode runs against no sub-shape
    at all and the sketch's Placement is left at identity.
    """
    for target in node.targets:
        if not (isinstance(target, ast.Attribute) and target.attr == "AttachmentSupport"):
            continue
        value = node.value
        if not isinstance(value, ast.List):
            continue
        for entry in value.elts:
            if (isinstance(entry, ast.Tuple) and len(entry.elts) == 2
                    and isinstance(entry.elts[1], ast.List) and not entry.elts[1].elts):
                return ast.unparse(node)
    return None


def _dimensional_target(node: ast.Assign):
    """The unparsed `feature.Attr` target of a dimensional assignment, or
    None -- `DIMENSION_ATTRS` is the closed set of PartDesign property names
    a drawing's own measurement fills, read off the recorded Steps this
    project has already applied."""
    for target in node.targets:
        if isinstance(target, ast.Attribute) and target.attr in DIMENSION_ATTRS:
            return target
    return None


def _numeric_literal(node):
    """The number a bare numeral or its unary-negated form spells, or None."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        node = node.operand
        sign = -1
    else:
        sign = 1
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return sign * node.value
    return None


def literal_bindings(tree) -> dict:
    """`name -> number` for every `name = <numeral>` in this Step, including
    a tuple unpacked from numerals one for one -- `L, D = 80.0, 30.0` -- so a
    bare numeral is still read as one whichever of the two spellings names
    it, not a reference the naming defeats."""
    bindings = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name):
            value = _numeric_literal(node.value)
            if value is not None:
                bindings[target.id] = value
        elif (isinstance(target, ast.Tuple)
              and isinstance(node.value, ast.Tuple)
              and len(target.elts) == len(node.value.elts)):
            for name_node, value_node in zip(target.elts, node.value.elts):
                if not isinstance(name_node, ast.Name):
                    continue
                value = _numeric_literal(value_node)
                if value is not None:
                    bindings[name_node.id] = value
    return bindings


def dimensional_literal(node: ast.Assign, bindings: dict):
    """
    The `(target, value)` of a dimensional assignment given a bare numeral,
    directly or through a name this Step bound one to, or None. A count, an
    index, a boolean and the handful of literals a construction genuinely
    needs -- 0, 1, a right angle, a full turn -- are not dimensions and are
    left alone.
    """
    target = _dimensional_target(node)
    if target is None:
        return None
    value = _numeric_literal(node.value)
    if value is None and isinstance(node.value, ast.Name):
        value = bindings.get(node.value.id)
    if value is None or value in _NOT_A_DIMENSION:
        return None
    return ast.unparse(target), value


def dimensional_default(node: ast.Assign):
    """The unparsed target of a dimensional assignment resolved with
    `.get(key, default)`, where a missing Design field silently becomes the
    default instead of failing, or None."""
    target = _dimensional_target(node)
    value = node.value
    if target is None or not isinstance(value, ast.Call):
        return None
    if isinstance(value.func, ast.Attribute) and value.func.attr == "get" and len(value.args) >= 2:
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
