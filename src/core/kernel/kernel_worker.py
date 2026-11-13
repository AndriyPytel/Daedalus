"""
The child half of the kernel: a headless FreeCAD child process started by
`core/kernel/kernel.py`, one JSON request per stdin line and one tagged
response per stdout line. `SENTINEL` must import without starting the
server, so `freecadcmd`'s renamed `__name__` is checked before serving starts, and a crash here is a normal outcome the
parent restarts from, with no shutdown message, because the parent kills
rather than asks; the kernel holds one live Document across many Steps, a
failed Step reopens the last checkpoint rather than continuing on a
Document a following Step should never have seen. Measured: 45 of 152
kernel calls failed across one review's sessions -- 15 on the Step's own
volume assert, 10 on a None deref -- so a failing Step is handed `volume`,
the idle-feature diagnosis and the Document's own objects, not only the
traceback.
"""
import contextlib
import difflib
import io
import json
import os
import shutil
import sys
import tempfile
import time

SENTINEL = "@@KERNEL@@"

_PROTOCOL_STREAM = None


def _isolate_protocol_stream():
    """
    Give the wire protocol a private fd before any native code can write
    into fd 1: `doc.recompute()`'s progress bar prints straight to the
    process's real stdout, with no newline after its last, unfinished
    fragment, so that text glues onto the front of the next tagged reply
    line -- the parent's reader drops the malformed line silently and both
    sides block on `read()` forever, one per Step. Native writes still land
    somewhere (stderr) rather than vanishing, and the wire keeps fd 1's
    original destination through the duplicated fd instead.
    """
    global _PROTOCOL_STREAM
    original = os.dup(1)
    os.dup2(2, 1)
    _PROTOCOL_STREAM = os.fdopen(original, "w", encoding="utf-8")


def respond(payload):
    stream = _PROTOCOL_STREAM or sys.stdout
    stream.write(SENTINEL + json.dumps(payload) + "\n")
    stream.flush()


def _ensure_on_path():
    """
    The installed addon leaves `sys.path` and this checkout's `src/` goes on
    it: `core` is a namespace package, so a second directory holding one
    merges in rather than losing, and what is bound from it answers imports.
    The sibling modules are imported inside the functions that use them, after this has run.
    """
    src = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path[:] = [entry for entry in sys.path
                   if entry != src and not os.path.isdir(os.path.join(entry, "core"))]
    sys.path.insert(0, src)
    for name, module in list(sys.modules.items()):
        if name != "core" and not name.startswith("core."):
            continue
        where = [getattr(module, "__file__", None) or ""] + list(getattr(module, "__path__", []))
        if any(w and not os.path.abspath(w).startswith(src + os.sep) for w in where):
            del sys.modules[name]


def summarise(doc):
    """
    The name, label and TypeId of every object. A PartDesign::Boolean also
    carries its `Type` (Fuse, Cut or Common) as `boolean_type`, because that
    property, not the TypeId, says which operation it performs.
    """
    return [
        {"name": o.Name, "label": o.Label, "type": o.TypeId,
         **({"boolean_type": o.Type} if o.TypeId == "PartDesign::Boolean" else {})}
        for o in doc.Objects
    ]


DERIVED = {"Shape"}

_EXCLUDED_FROM_DIFF = {"Name", "Label"} | DERIVED


def _property_value(obj, prop):
    """
    A sketch's geometry is read as its serialised `Content`, since every read
    of `Geometry` hands back fresh wrappers that never compare equal.
    """
    try:
        value = getattr(obj, prop)
    except Exception:
        return None
    kind = getattr(obj, "getTypeIdOfProperty", lambda _: None)(prop)
    if kind == "Part::PropertyGeometryList":
        return [geometry.Content for geometry in value]
    return value


def _comparable(obj, prop):
    """
    Whether *prop* is worth diffing by value.

    `ShapeMaterial` hands back a fresh wrapper on every read with no
    value equality, and a `Part::PropertyPartShape` is a computed shape that
    does the same, so both are excluded by FreeCAD property type, not name.
    """
    try:
        kind = obj.getTypeIdOfProperty(prop)
        return "Material" not in kind and kind != "Part::PropertyPartShape"
    except Exception:
        return True


def _native():
    """The one seam into nativeifc: what a Step does to the IFC a Document holds is read and written through it."""
    from core import nativeifc

    return nativeifc


def _properties_snapshot(doc):
    """
    Every object's own `PropertiesList`, by value, `Name`/`Label` and the
    derived properties in `DERIVED` left out -- the exclusion rule
    `fcc-research-doc-diff.md` demonstrated, so a rename or a recompute never
    reads as a modification.
    """
    snapshot = {}
    ifc = _native().fingerprints(doc)
    for obj in doc.Objects:
        properties = getattr(obj, "PropertiesList", None) or ()
        snapshot[obj.Name] = {
            prop: _property_value(obj, prop)
            for prop in properties
            if prop not in _EXCLUDED_FROM_DIFF and _comparable(obj, prop)
        }
        if obj.Name in ifc:
            snapshot[obj.Name]["IFC"] = ifc[obj.Name]
    return snapshot


def _changed_properties(before, after):
    changed = set()
    for prop in set(before) | set(after):
        if prop not in before or prop not in after:
            changed.add(prop)
            continue
        try:
            if before[prop] != after[prop]:
                changed.add(prop)
        except Exception:
            changed.add(prop)
    return changed


class _Touched:
    """
    What the Step did, observed off the Document rather than recorded through
    a call this process hooked.

    Diff runs before recompute, not after: a computed property like `Shape`
    changes for every object downstream the moment recompute runs.
    """

    def __init__(self, doc):
        self._before = _properties_snapshot(doc)
        self.created = set()
        self.deleted = set()
        self._changed = {}

    def diff(self, doc):
        after = _properties_snapshot(doc)
        self.created = set(after) - set(self._before)
        self.deleted = set(self._before) - set(after)
        self._changed = {
            name: _changed_properties(self._before[name], after[name])
            for name in set(self._before) & set(after)
        }

    def modified(self):
        return {name for name, properties in self._changed.items() if properties}

    def touched(self):
        return bool(self.created or self.deleted or self.modified())


_HINT_LIMIT = 60


def _name_of(obj):
    """
    What to call the thing in the hint.

    TypeId is the useful name for a DocumentObject, but on a class it is an
    unbound descriptor whose repr -- <attribute 'TypeId' of 'Base.BaseClass'
    objects> -- named nothing the assistant could act on.
    """
    type_id = getattr(obj, "TypeId", None)
    if isinstance(type_id, str):
        return type_id
    return getattr(obj, "__name__", None) or type(obj).__name__


def _what_the_document_holds(doc, name):
    """
    Answer a dereference of `None` with the objects that do exist.

    `'NoneType' object has no attribute 'Shape'` names neither the lookup that
    failed nor what it could have found, and FreeCAD renames an object whose
    Name is taken, so asked-for and allocated names routinely differ.
    """
    if doc is None:
        return ""
    try:
        held = ", ".join("%s (%s, %s)" % (o["name"], o["label"], o["type"])
                         for o in summarise(doc))
    except BaseException:
        return ""
    return ("\nThe lookup before %r returned None -- nothing in the Document answered it."
            "\nThe Document holds: %s" % (name, held))


def introspect(exc, doc=None):
    """
    Answer an AttributeError with what the object actually has. FreeCAD
    names only the missing attribute, so a second guess burns Step
    budget; introspection answers it here rather than hoping the
    assistant asks first. PropertiesList suits a DocumentObject (`dir()`
    is eighty names of plumbing); a module/class gets the unbound
    descriptor instead.
    """
    obj, name = getattr(exc, "obj", None), getattr(exc, "name", None)
    if not name:
        return ""
    if obj is None:
        return _what_the_document_holds(doc, name)
    try:
        public = sorted(a for a in dir(obj) if not a.startswith("_"))
        declared = getattr(obj, "PropertiesList", None)
        properties = sorted(declared) if isinstance(declared, (list, tuple)) else public
        hint = "\n%s has no %r." % (_name_of(obj), name)
        close = difflib.get_close_matches(name, public, n=3)
        if close:
            hint += " Closest existing: %s." % ", ".join(close)
        return hint + "\nIt has: %s" % ", ".join(properties[:_HINT_LIMIT])
    except BaseException:
        return ""


def _geometry_of(obj):
    """
    Volume plus the BoundBox corners for one feature.

    Not `Shape.hashCode()`: it is stable for one live Shape but changes
    across a checkpoint save/reload on identical geometry. Volume and
    BoundBox are exactly reproduced after a save/reload/recompute.
    """
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return None
    box = shape.BoundBox
    return (shape.Volume, box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax)


def _geometry_snapshot(doc):
    return {o.Name: _geometry_of(o) for o in doc.Objects}


STDOUT_LIMIT = 4000


def _capturing():
    """
    Somewhere for a Step's own output to go that is still a real text stream:
    `sys.stdout.buffer.write` is what a Step is free to call, and a bare
    `StringIO` has no `buffer` at all.
    """
    return io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="replace",
                            write_through=True)


def _printed(said):
    """
    What a Step's code wrote to stdout, capped and marked where it was cut.

    A Step's print is the one channel an agent has for looking at the Document
    -- its answer says what changed, not what is there -- and the one field of
    that answer the model can grow without limit.
    """
    said.flush()
    text = said.buffer.getvalue().decode("utf-8", "replace")
    if len(text) <= STDOUT_LIMIT:
        return text
    return text[:STDOUT_LIMIT] + "\n...cut here, %d characters in all" % len(text)


def _recompute_scope(doc, touched):
    """
    What a Step's own recompute should touch: its writes, resolved to
    live objects, plus everything downstream via `InList` -- unlike
    `doc.recompute(objs)` alone, which skips it and would leave an edited
    feature stale (not `Invalid`) past the failure scan. `None` asks for
    the full recompute instead.
    """
    names = touched.created | touched.modified()
    roots = [o for o in (doc.getObject(name) for name in names) if o is not None]
    if not roots:
        return None
    seen = set()
    scope = []
    stack = list(roots)
    while stack:
        obj = stack.pop()
        if obj.Name in seen:
            continue
        seen.add(obj.Name)
        scope.append(obj)
        stack.extend(obj.InList)
    return scope


SCRATCH = "KERNEL_SCRATCH"

_SESSION = {"doc": None, "checkpoint": None, "names": {}, "scratch": None}


def _scratch():
    """
    The directory this process keeps its checkpoint in: the one its parent made and will remove,
    or one of its own that it removes on the way out.
    """
    if _SESSION.get("scratch") is None:
        _SESSION["scratch"] = os.environ.get(SCRATCH) or tempfile.mkdtemp(prefix="kernel_checkpoint_%d_" % os.getpid())
    return _SESSION["scratch"]


def _checkpoint_path():
    if _SESSION["checkpoint"] is None:
        _SESSION["checkpoint"] = os.path.join(_scratch(), "checkpoint.FCStd")
    return _SESSION["checkpoint"]


def _close_every_document():
    import FreeCAD

    for doc in list(FreeCAD.listDocuments().values()):
        FreeCAD.closeDocument(doc.Name)


def _open_document(path):
    import FreeCAD

    _close_every_document()
    return FreeCAD.openDocument(path) if path else FreeCAD.newDocument("Kernel")


def _reload_checkpoint():
    """
    Undo a failed Step by closing the live Document and reopening the last
    checkpoint -- the Document a following Step sees is exactly what it would
    have been had the failed Step never run.
    """
    import FreeCAD

    path = _SESSION["checkpoint"]
    _close_every_document()
    _SESSION["doc"] = (FreeCAD.openDocument(path) if path and os.path.exists(path)
                       else FreeCAD.newDocument("Kernel"))


def _checkpoint_exists():
    return _SESSION["checkpoint"] is not None and os.path.exists(_SESSION["checkpoint"])


def _save_checkpoint(doc):
    """
    `saveCopy` is a silent no-op when the destination equals `doc.FileName`
    -- which is exactly what it becomes after `_reload_checkpoint` opens the
    checkpoint itself. Saving to a sibling temp path first sidesteps that,
    and `os.replace` is atomic, so a reader never sees a half-written file.
    """
    path = _checkpoint_path()
    tmp_path = os.path.join(os.path.dirname(path), "saving.FCStd")
    _native().save_projects(doc, _scratch())
    doc.saveCopy(tmp_path)
    os.replace(tmp_path, path)


def _run_step_body(doc, namespace, touched, request):
    """
    Execute a Step's code against *doc* and the checks that make it wrong.

    Raises on any failure -- an `AssertionError` from the code itself, or a
    `RuntimeError` from a check below -- for `run_step` to catch and
    translate into a checkpoint reload. Returns the success payload.
    """
    from core.kernel.step_namespace import register_step_source, signature_of
    from core.kernel.step_rules import _raise_if_broken, _raise_if_mixed_concavity, _volume_moved

    register_step_source(request["code"])
    exec(compile(request["code"], "<step>", "exec"), namespace)  # noqa: S102
    touched.diff(doc)

    _raise_if_mixed_concavity(doc)

    if touched.touched():
        scope = _recompute_scope(doc, touched)
        if scope is None:
            doc.recompute()
        else:
            doc.recompute(scope)

    if touched.touched() or "document" in request:
        _raise_if_broken(doc, touched)

    changed = touched.touched() or "document" in request
    result = {
        "ok": True,
        "objects": summarise(doc),
        "created": sorted(touched.created),
        "deleted": sorted(touched.deleted),
        "modified": sorted(touched.modified()),
        "geometry": _geometry_snapshot(doc) if changed else {},
        "volume": _volume_moved(doc, touched.created),
        "signature": signature_of(doc) if changed else {},
    }
    render_path = request.get("render_path")
    if render_path:
        from core.kernel.preflight_render import render

        result["render"] = render(doc, render_path, view=request.get("render_view") or "iso")
    return result


def run_step(request):
    """
    Run one Step against the kernel's live Document. A `document` in
    *request* is a fresh handoff: the session starts over from that path,
    with an empty name map. Pass writes the checkpoint;
    fail reopens the last checkpoint only when `_Touched.touched()` says
    the Step actually changed the Document, since a read-only probe that
    raises leaves it exactly as it was; `exploring=True` marks a call no
    Step declared, so a pass that touched the Document is reopened the
    same as a failed one, and the response names what was rolled back.
    """
    from core.kernel.step_namespace import _NamedDoc, step_namespace, trimmed_traceback
    from core.kernel.step_rules import _idle_feature_report, _past_the_tip, _volume_moved

    exploring = request.get("exploring", False)
    if "document" in request or _SESSION["doc"] is None:
        _SESSION["doc"] = _open_document(request.get("document"))
        _SESSION["names"] = {}
        _save_checkpoint(_SESSION["doc"])

    doc = _SESSION["doc"]
    names = dict(_SESSION["names"])
    namespace = step_namespace(_NamedDoc(doc, names))
    namespace["names"] = names

    touched = _Touched(doc)
    said = _capturing()
    try:
        with contextlib.redirect_stdout(said):
            response = _run_step_body(doc, namespace, touched, request)
    except AssertionError as exc:
        touched.diff(doc)
        response = {"ok": False,
                    "traceback": trimmed_traceback(exc) + _idle_feature_report(doc, touched.created),
                    "volume": _volume_moved(doc, touched.created)}
    except BaseException as exc:
        touched.diff(doc)
        response = {"ok": False,
                    "traceback": (trimmed_traceback(exc) + introspect(exc, doc)
                                  + _idle_feature_report(doc, touched.created)),
                    "volume": _volume_moved(doc, touched.created)}
    printed = _printed(said)
    if printed:
        response["stdout"] = printed

    past = _past_the_tip(doc, touched.created)
    if past:
        response["ok"] = False
        response["traceback"] = (response.get("traceback") or "") + past
    if response.get("ok"):
        if exploring and touched.touched():
            _reload_checkpoint()
            response["rolled_back"] = sorted(touched.created | touched.deleted | touched.modified())
        else:
            if touched.touched() or not _checkpoint_exists():
                _save_checkpoint(doc)
            _SESSION["names"] = names
    else:
        if touched.touched():
            _reload_checkpoint()
    response["checkpoint"] = _SESSION["checkpoint"]
    response["names"] = dict(_SESSION["names"])
    return response


_ASKED_FOR = {"step": run_step}


def _parent_is_gone(parent):
    """
    Whether *parent* has exited, given a second to be reparented away from.
    A parent that only closed this process's stdin to restart it still owns, and resumes from, the checkpoint.
    """
    deadline = time.monotonic() + 1.0
    while os.getppid() == parent and time.monotonic() < deadline:
        time.sleep(0.05)
    return os.getppid() != parent


def serve(stdin=None, respond=respond):
    """
    Read requests off stdin until it closes, and answer each one.

    What the process will do is the `do` of the request; a request that does
    not say is a Step. Anything Python can still catch comes back as a
    correction, and anything it cannot takes this process down instead of
    FreeCAD.
    """
    _ensure_on_path()
    from core.kernel.step_namespace import trimmed_traceback

    _isolate_protocol_stream()
    parent = os.getppid()
    _SESSION["scratch"] = os.environ.get(SCRATCH)
    stdin = stdin or sys.stdin
    respond({"ready": True, "pid": os.getpid()})

    try:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except ValueError as exc:
                respond({"ok": False, "traceback": "malformed request: " + str(exc)})
                continue
            try:
                respond(_ASKED_FOR[request.get("do") or "step"](request))
            except BaseException as exc:
                respond({"ok": False, "traceback": trimmed_traceback(exc) + introspect(exc)})
    finally:
        if _SESSION.get("scratch") and (SCRATCH not in os.environ or _parent_is_gone(parent)):
            shutil.rmtree(_SESSION["scratch"], ignore_errors=True)


if __name__ in ("__main__", "kernel_worker"):
    serve()
