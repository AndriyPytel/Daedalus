"""The Assertion phase: check acceptance criteria."""
import json
import os
from dataclasses import dataclass, field

from core.events import ASSERTION, TOOL_CALL, TOOL_RESULT

import tempfile

from core.agent.agent import run_declaring
from core.agent import phase_contract as pc
from core.agent.subagent import SessionTreeSupervisor
from core.kernel import Kernel
from core.loop.budgets import Budgets
from core.loop.document import copied
from core.loop.mechanical import covered_entries, envelope_figures, the_collateral_measured, the_sizes_measured
from core.surfaces import LINEAR
from core.loop.execute import _DID_NOT_HOLD, _MODEL_IS_INVALID
from core.kernel.kernel import TIMED_OUT
from core.loop.expressions import TOO_SLOW, answers_in, each_expression_evaluated, progress_read
from core.loop.prompt import ASSERTION_PHASE, prompt
from core.prompts import read_prompt
from core.loop.results import Stopped


@dataclass
class Checked:
    """
    What the Assertion phase made of the finished Document.

    Constraint: `confirmed` is read back from the check's own `held` list, so a run can carry both a non-empty `confirmed` and a non-empty `unmet`.
    `defects` names the entries whose expression never gave True or False, and they are in `uncovered` too, never in `confirmed`.
    """

    failed: bool = False
    traceback: str = ""
    confirmed: list = field(default_factory=list)
    uncovered: list = field(default_factory=list)
    unmet: list = field(default_factory=list)
    defects: list = field(default_factory=list)
    geometry: dict = field(default_factory=dict)


def the_envelope_measured(acceptance, description=None):
    """
    The check nobody had to write: the part's overall sizes against the sides of its exact box, within `LINEAR` since the box still lands inside the true extreme on a curved face.
    Exactly three sizes are sorted against the sorted sides, so it never guesses orientation from geometry; any other number each has to be one of the sides, since `width` and `depth front to rear` often say one size twice.
    Measured: a lug built 25mm tall for 30 drawn passed Preflight.
    """
    sizes = sorted(figure for _, figure in envelope_figures(acceptance, description))
    if not sizes:
        return ""
    if len(sizes) == 3:
        return _ENVELOPE_CHECK % (sizes, LINEAR, sizes, LINEAR)
    return _ENVELOPE_MEMBERS_CHECK % (sorted(set(sizes)), LINEAR, LINEAR)


_ENVELOPE_CHECK = """
shape = _faces.shape
box = measure.box(shape)
built = sorted([box.XLength, box.YLength, box.ZLength])
asked = %s
assert all(abs(a - b) <= %s for a, b in zip(built, asked)), (
    'these did not hold: the envelope. The description asks for a part measuring '
    + ' x '.join('%%.2f' %% one for one in %s) + ' mm overall and the one built '
    'measures ' + ' x '.join('%%.2f' %% one for one in built) + ' mm, which is more '
    'than %s mm out. The sizes are sorted, so this says nothing about which way '
    'round it was built - only that it is not that part.')
"""


_ENVELOPE_MEMBERS_CHECK = """
shape = _faces.shape
box = measure.box(shape)
built = sorted([box.XLength, box.YLength, box.ZLength])
asked = %s
missing = [a for a in asked if not any(abs(a - b) <= %s for b in built)]
assert not missing, (
    'these did not hold: the envelope. The part measures '
    + ' x '.join('%%.2f' %% one for one in built) + ' mm and no side is within %s mm of '
    + ', '.join('%%.2f' %% one for one in missing) + ' mm.')
"""


def the_topology_measured(description, base=None):
    """
    Betti numbers of the finished Document checked against b0 == 1 and, where at least one feature carries the `goes_through` flag, b1 == the instance count of the features whose flag is true (a feature's `count` value, 1 when absent or not a positive integer); unflagged features add nothing and the prose `topology` field is never read.
    With a *base* part only b0 is checked: Design describes the features an edit changes, never the whole part, so its through count is not the part's.
    """
    features = [feature for feature in (description or {}).get("features") or ()
               if isinstance(feature, dict)]
    through_check = ""
    if not base and any("goes_through" in feature for feature in features):
        expected = sum(_instances(feature) for feature in features if feature.get("goes_through") is True)
        through_check = _THROUGH_FEATURE_CHECK % (expected, expected)
    return _TOPOLOGY_CHECK % through_check


def _instances(feature):
    """The pattern size of a feature, read from its `count`, a bare integer or a dict with `value`; 1 when that is missing or not a positive integer."""
    value = feature.get("count")
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return max(int(value), 1)
    except (TypeError, ValueError):
        return 1


_TOPOLOGY_CHECK = """
from core.topology import betti_numbers
shape = _faces.shape
bbox = shape.BoundBox
deflection = 0.001 * bbox.DiagonalLength
points, facets = shape.tessellate(deflection)
vertices = [(p.x, p.y, p.z) for p in points]
b0, b1, b2 = betti_numbers(vertices, facets)
assert b0 == 1, (
    'these did not hold: the topology. The part is meant to be one solid body '
    'and the one built comes back in ' + str(b0) + '.')
%s
"""


_THROUGH_FEATURE_CHECK = """assert b1 == %d, (
    'these did not hold: the through-features. expected %d through features, '
    'the solid has ' + str(b1) + '.')
"""


SELF_INTERSECTION_CHECK = """
import os
import tempfile
from OCC.Core.BOPAlgo import BOPAlgo_ArgumentAnalyzer
from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepTools import breptools
from OCC.Core.TopoDS import TopoDS_Shape

held = []
failed = []
for obj in doc.Objects:
    shape = getattr(obj, 'Shape', None)
    if shape is None or shape.isNull() or not shape.Solids:
        continue
    group = obj.getParentGeoFeatureGroup() if hasattr(obj, 'getParentGeoFeatureGroup') else None
    if group is not None and group.TypeId == 'PartDesign::Body':
        continue
    handle, path = tempfile.mkstemp(suffix='.brep')
    os.close(handle)
    try:
        shape.exportBrep(path)
        occ_shape = TopoDS_Shape()
        breptools.Read(occ_shape, path, BRep_Builder())
    finally:
        os.remove(path)
    analyzer = BOPAlgo_ArgumentAnalyzer()
    analyzer.SetShape1(occ_shape)
    analyzer.SetSelfInterMode(True)
    analyzer.Perform()
    if analyzer.HasFaulty():
        failed.append(obj.Name + " self-intersects (BOPAlgo_SelfIntersect)")
    else:
        held.append(obj.Name)
assert not failed, "these did not hold: " + "; ".join(failed)
"""


class _LazyKernel:
    """
    A Kernel not booted until an Assertion attempt runs code, shared across
    every attempt of one `assertion()` call so a retry reuses the subprocess
    and the open Document instead of booting and copying afresh. A retry that
    never writes code (`declare` straight away) still boots nothing.
    """

    def __init__(self):
        self._real = None

    def check(self, *args, **kwargs):
        if self._real is None:
            self._real = Kernel()
        return self._real.check(*args, **kwargs)

    def kill(self):
        if self._real is not None:
            self._real.kill()


def fixed_checks(acceptance, description, base):
    """The (script, name) pairs every finished Document is put through; a script is empty where it does not apply, and an edit of a *base* part skips the self-intersection scan."""
    return [(the_envelope_measured(acceptance, description), "the envelope"),
            ("" if base else SELF_INTERSECTION_CHECK, "no solid self-intersects"),
            (the_topology_measured(description, base), "the topology"),
            (the_collateral_measured(acceptance, base), "no collateral change"),
            (the_sizes_measured(acceptance, description), "the sizes, axes and counts")]


def assertion(acceptance, described, tools, llm, events, budgets=None, cancelled=None,
              after_a_retry=(), supervisor=None, kernel=None, description=None, base=None,
              not_checked=()):
    """
    Writes and runs acceptance checks against the finished Document,
    read-only, once per run, given `description` -- Design's own prose,
    never `described`'s rendering of what Execute built -- since the
    acceptance list already flattens the one and the built Document only
    shows the other. A context of its own -- Review is deliberately not
    told how the result was produced. Measured (warm worker): ~0.3s first
    solid, then ~0.7ms/solid marginal.
    """
    budgets = budgets or Budgets()
    covered = covered_entries(acceptance, description, base)
    remaining = [entry for entry in acceptance if entry not in covered]
    request = {"the_document_as_it_now_stands": described}
    if base:
        request["the_base_part"] = base
    if after_a_retry:
        request["entries_a_retry_touched"] = list(after_a_retry)

    cancelled = cancelled or (lambda: False)

    fixed = fixed_checks(acceptance, description, base)
    scripts = [code for code, _ in fixed if code]
    fixed_confirmed = []
    geometry = {}
    if scripts:
        code = "\n".join(scripts)
        events.emit(TOOL_CALL, name="check_assertions", arguments={"code": code})
        result = tools.call("check_assertions", {"code": code})
        events.emit(TOOL_RESULT, name="check_assertions", result=result)
        if not result.get("ok"):
            traceback = result.get("traceback") or "the fixed checks failed."
            unmet = _unmet_in(traceback) or ["the envelope and self-intersection checks"]
            events.emit(ASSERTION, left_out=list(not_checked), ok=False, code=code, confirmed=[],
                        uncovered=[], traceback=traceback, unmet=unmet)
            return Checked(failed=True, traceback=traceback, unmet=unmet,
                           geometry=result.get("geometry") or {})
        fixed_confirmed = [name for code, name in fixed if code]
        geometry = result.get("geometry") or {}

    if not acceptance:
        return None

    if not remaining:
        events.emit(ASSERTION, left_out=list(not_checked), ok=True, code="\n".join(scripts), confirmed=covered,
                    uncovered=[], traceback="")
        return Checked(confirmed=covered, geometry=geometry)

    held, failed, uncovered, defects = set(), set(), set(), {}
    asked = list(range(len(remaining)))
    expressions = {}
    working_kernel = kernel or _LazyKernel()
    try:
        with copied(tools) as document:
            for attempt in range(budgets.attempts_per_step):
                if cancelled():
                    raise Stopped()
                request["acceptance"] = [{"entry": entry, "text": remaining[entry]} for entry in asked]
                if defects:
                    request["your_checks_that_raised"] = [
                        {"entry": entry, "expr": expressions.get(entry, ""), "error": error}
                        for entry, error in sorted(defects.items())]

                supervisor = supervisor or SessionTreeSupervisor()
                task = json.dumps(request, indent=2, default=str)

                run_kwargs = {"kernel": working_kernel, "supervisor": supervisor,
                              "stream": events, "system": prompt(ASSERTION_PHASE)}
                if attempt == 0:
                    run_kwargs["document"] = document
                if description:
                    run_kwargs["design"] = description
                declared, _ = run_declaring(pc.ASSERTION, {}, llm, task, **run_kwargs)

                answered, left_out, unanswered = answers_in(declared, asked)
                uncovered.update(left_out)
                expressions.update(answered)
                defects = {entry: "you gave no answer for this entry" for entry in unanswered}
                if answered:
                    code, result = _expressions_evaluated(answered, tools, events)
                    geometry = result.get("geometry") or geometry
                    if not result.get("ok"):
                        traceback = result.get("traceback") or "The checks failed without saying why."
                        if _MODEL_IS_INVALID in traceback:
                            return _failed_check(events, code, covered, held, failed, uncovered,
                                                 remaining, traceback, geometry, list(remaining),
                                                 not_checked=not_checked)
                        defects.update({entry: traceback for entry in answered})
                    else:
                        failed.update(result.get("failed") or ())
                        defects.update({entry: error for entry, error in result.get("defects") or ()})
                        held.update(result["held"] if result.get("held") is not None else
                                    [entry for entry in answered if entry not in failed and entry not in defects])
                asked = sorted(defects)
                if not asked:
                    break
    finally:
        if kernel is None:
            working_kernel.kill()

    confirmed = covered + [remaining[entry] for entry in sorted(held)]
    if asked:
        confirmed = fixed_confirmed + confirmed
    unanswerable = [remaining[entry] for entry in sorted(uncovered) + asked]
    if failed:
        return _failed_check(events, "", covered, held, failed, uncovered, remaining,
                             _DID_NOT_HOLD + "; ".join(remaining[entry] for entry in sorted(failed)),
                             geometry, [remaining[entry] for entry in sorted(failed)],
                             unanswerable, [remaining[entry] for entry in asked], not_checked)
    nothing_judged = asked and not held and not uncovered
    events.emit(ASSERTION, left_out=list(not_checked), ok=None if nothing_judged else True, code="", confirmed=confirmed,
                uncovered=unanswerable, traceback="", defects=[remaining[entry] for entry in asked])
    return Checked(confirmed=confirmed, uncovered=unanswerable,
                   defects=[remaining[entry] for entry in asked], geometry=geometry)


def _expressions_evaluated(answered, tools, events):
    """
    Evaluates *answered* in as many kernel calls as it takes: `(code, result)` of the last call, its lists holding every entry's outcome.
    A call the kernel times out keeps the entries it finished, makes the entry in flight a defect, and leaves the rest to a new call.
    """
    pending, held, failed, defects = dict(answered), [], [], []
    with tempfile.TemporaryDirectory() as folder:
        while True:
            path = os.path.join(folder, "progress_%d.jsonl" % len(held + failed + defects))
            code = each_expression_evaluated(pending, path)
            events.emit(TOOL_CALL, name="check_assertions", arguments={"code": code})
            result = tools.call("check_assertions", {"code": code})
            events.emit(TOOL_RESULT, name="check_assertions", result=result)
            if result.get("ok"):
                done = result["held"] if result.get("held") is not None else [
                    entry for entry in pending if entry not in (result.get("failed") or ())
                    and entry not in dict(result.get("defects") or ())]
                return code, dict(result, held=held + list(done), failed=failed + list(result.get("failed") or ()),
                                  defects=defects + list(result.get("defects") or ()))
            traceback = result.get("traceback") or ""
            if _MODEL_IS_INVALID in traceback or not (held or failed or defects) and TIMED_OUT not in traceback:
                return code, result
            if TIMED_OUT in traceback:
                done_held, done_failed, done_defects, in_flight = progress_read(path)
                done_defects += [[in_flight, TOO_SLOW]] if in_flight is not None else []
            else:
                done_held, done_failed, done_defects = [], [], [[entry, traceback] for entry in pending]
            held, failed, defects = held + done_held, failed + done_failed, defects + done_defects
            reached = set(done_held + done_failed) | {entry for entry, _ in done_defects}
            if not reached:
                defects += [[entry, traceback] for entry in pending]
                return code, dict(result, ok=True, held=held, failed=failed, defects=defects)
            pending = {entry: text for entry, text in pending.items() if entry not in reached}
            if not pending:
                return code, dict(result, ok=True, held=held, failed=failed, defects=defects)


def _failed_check(events, code, covered, held, failed, uncovered, remaining, traceback,
                  geometry, unmet, unanswerable=(), defects=(), not_checked=()):
    confirmed = covered + [remaining[entry] for entry in sorted(held)]
    events.emit(ASSERTION, left_out=list(not_checked), ok=False, code=code, confirmed=confirmed, uncovered=list(unanswerable),
                traceback=traceback, unmet=unmet, defects=list(defects))
    return Checked(failed=True, confirmed=confirmed, traceback=traceback,
                   uncovered=list(unanswerable), unmet=unmet, defects=list(defects),
                   geometry=geometry)


def _unmet_in(traceback):
    """
    The acceptance entries a failed check named, one per entry.
    Fallback for a failure the worker's own `failed` list did not cover — the envelope check, or a script that raised outside assertion.md's shape.

    The checks end in one assert listing every failed entry by semicolons, per that prompt's spec; only the `AssertionError` line is read, never the source line a traceback echoes above it.
    """
    for line in traceback.strip().splitlines():
        if not line.startswith("AssertionError") or _DID_NOT_HOLD not in line:
            continue
        listed = line.split(_DID_NOT_HOLD, 1)[1]
        return [entry.strip() for entry in listed.split(";") if entry.strip()]
    return []


def _the_model_failed(traceback):
    """
    Whether this traceback is about the part or about the code that checked it.
    An `AssertionError` names a failed entry; a Document that will not
    recompute is a part broken before anything was measured, and needs
    different feedback. Any line, not the last: the assert can list forty
    failed entries across lines, but only the exception line is unindented.
    """
    if _MODEL_IS_INVALID in traceback:
        return True
    return any(line.startswith("AssertionError")
               for line in traceback.strip().splitlines())


def _why_the_checks_failed(traceback):
    """The feedback Plan gets, which must not say "a dimension is wrong" of a broken part."""
    if _MODEL_IS_INVALID in traceback:
        return ("The acceptance checks could not run: the Document does not recompute "
                "cleanly, so the part is in error before anything could be measured.\n"
                + traceback)
    return ("The result does not meet its acceptance list. The check that failed says "
            "which entry:\n" + traceback)


_GEOMETRY_TOLERANCE = 1e-6


def _geometry_close(before, now):
    return all(abs(a - b) <= _GEOMETRY_TOLERANCE * max(1.0, abs(b))
               for a, b in zip(before, now))


def _geometry_regressions(baseline, geometry, retried_names):
    """
    Features whose geometry moved since the last held Assertion pass, and
    are not one of the exact Names a retried Step actually touched.
    `retried_names` matches FreeCAD `Object.Name` exactly, never a prefix or
    substring — "Slot" never excuses "SlotChamfer". `baseline` is empty on
    the first Assertion pass of a run, which reports nothing moved.
    """
    touched = set(retried_names or ())
    moved = []
    for name, was in (baseline or {}).items():
        if name in touched:
            continue
        now = (geometry or {}).get(name)
        if now is None or was is None or _geometry_close(was, now):
            continue
        moved.append(name)
    return sorted(moved)


_GEOMETRY_REGRESSION = read_prompt("geometry_regression.md")
