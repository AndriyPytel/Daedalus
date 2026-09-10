"""The Assertion phase: check acceptance criteria."""
import json
from dataclasses import dataclass, field

from core.design.core import UNDER
from core.events import ASSERTION, TOOL_CALL, TOOL_RESULT

import tempfile

from core.agent.agent import run_phase
from core.agent import phase_contract as pc
from core.agent.subagent import SessionTreeSupervisor
from core.loop.budgets import Budgets
from core.loop.describe import _A_SIZE, _TRUE_TO
from core.loop.document import copied
from core.loop.execute import _DID_NOT_HOLD, _MODEL_IS_INVALID, _addressed
from core.loop.prompt import ASSERTION_PHASE, prompt
from core.prompts import read_prompt
from core.loop.results import Stopped


@dataclass
class Checked:
    """
    What the Assertion phase made of the finished Document.

    `failed` is a model that did not meet its acceptance list; `traceback` is
    what Plan is told. A check that could not be written is neither passed nor
    failed — `confirmed` is empty, `failed` is False, and Review judges the
    whole list alone.

    `confirmed` is what the check script itself measured and found true, read
    back out of its own `held` list rather than inferred as everything the
    model did not name `uncovered` — so it is not only the success branch's:
    a run whose checks failed on some entries and held on others carries both
    a non-empty `confirmed` and a non-empty `unmet`.

    `unmet` is the entries that did not hold, named one by one. The traceback
    says the same thing in prose, and prose is what Plan reads; this is what
    survives the end of the run.

    `geometry` is the per-feature fingerprint `check_assertions` measured
    alongside whatever it was asked to check — Volume and BoundBox,
    read back off `KernelResult` the same way `confirmed` reads `held`.
    Empty wherever no `check_assertions` call ran, which the fingerprint
    comparison at the call site reads as "nothing to compare yet" rather than
    "everything moved".
    """

    failed: bool = False
    traceback: str = ""
    confirmed: list = field(default_factory=list)
    uncovered: list = field(default_factory=list)
    unmet: list = field(default_factory=list)
    geometry: dict = field(default_factory=dict)


def the_envelope_measured(acceptance):
    """
    The check nobody had to write: the part's three overall sizes.

    Preflight measures topology — objects in error, a feature that moved no
    material, a Body in pieces — and never a dimension, so a lug built 25 mm
    tall where the drawing says 30, and a split that stops 16 mm short of the
    end face it has to reach, both came back as success
    (`logs/sessions/549e6232c9e04b659e5a2d536354bd7a.jsonl`).

    Sorted against sorted, deliberately. Which way round the part was built is
    not something anything in this codebase knows — the description names its
    directions in words and the Document has X, Y and Z — and a check that had
    to know would be a check that guessed. What it can say without guessing is
    that a part 108 x 30 x 30 has been built and not one 108 x 30 x 50, which
    is the failure worth catching and the one an assertion written by a
    provider is least likely to cover.

    Empty where the envelope does not carry three plain sizes: a part described
    in two directions, or in words with no millimetres in them, is not one this
    can measure, and inventing a third would fail every run that has one.

    `_A_SIZE` is what a size looks like in the words the envelope writes them
    in, and `_TRUE_TO` is how close a built one has to be: half a millimetre,
    because a drawing this reads is dimensioned to a tenth and the fillets on
    an outer contour move a bounding box by less than that.

    `_ENVELOPE_CHECK` is written out rather than built up, because it is read
    by whoever reads a failed run. It measures `optimalBoundingBox`, since a
    triangulated one lands inside the true extreme on a curved face, which is
    the whole of ADR-0022's argument.
    """
    sizes = []
    for address, value in _addressed(acceptance).items():
        if address.startswith("envelope" + UNDER):
            found = _A_SIZE.findall(value)
            if len(found) == 1:
                sizes.append(float(found[0]))
    if len(sizes) != 3:
        return ""
    return _ENVELOPE_CHECK % (sorted(sizes), _TRUE_TO, sorted(sizes), _TRUE_TO)


_ENVELOPE_CHECK = """
solids = [o for o in doc.Objects if getattr(o, 'Shape', None) is not None
          and o.Shape.Solids and not o.Shape.isNull()]
assert solids, 'the acceptance names an envelope and the Document has no solid'
shape = max(solids, key=lambda o: o.Shape.Volume).Shape
box = shape.optimalBoundingBox()
built = sorted([box.XLength, box.YLength, box.ZLength])
asked = %s
assert all(abs(a - b) <= %s for a, b in zip(built, asked)), (
    'these did not hold: the envelope. The description asks for a part measuring '
    + ' x '.join('%%.2f' %% one for one in %s) + ' mm overall and the one built '
    'measures ' + ' x '.join('%%.2f' %% one for one in built) + ' mm, which is more '
    'than %s mm out. The sizes are sorted, so this says nothing about which way '
    'round it was built - only that it is not that part.')
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


def assertion(acceptance, described, tools, llm, events, budgets=None, cancelled=None,
              after_a_retry=(), supervisor=None, kernel=None):
    """Writes and runs acceptance checks against the finished Document — read-only, once per run.

    Constraints: a context of its own — Review is deliberately not told how the result was produced
    (ADR-0003). Measured (warm worker): ~0.3s first solid, then ~0.7ms/solid marginal.
    """
    budgets = budgets or Budgets()
    request = {"acceptance": list(acceptance),
               "the_document_as_it_now_stands": described}
    if after_a_retry:
        request["entries_a_retry_touched"] = list(after_a_retry)

    cancelled = cancelled or (lambda: False)

    for label, measured in (("the envelope check", the_envelope_measured(acceptance)),
                            ("the self-intersection check", SELF_INTERSECTION_CHECK)):
        if not measured:
            continue
        events.emit(TOOL_CALL, name="check_assertions", arguments={"code": measured})
        result = tools.call("check_assertions", {"code": measured})
        events.emit(TOOL_RESULT, name="check_assertions", result=result)
        if not result.get("ok"):
            traceback = result.get("traceback") or (label + " failed.")
            unmet = _unmet_in(traceback) or [label]
            events.emit(ASSERTION, ok=False, code=measured, confirmed=[],
                        uncovered=[], traceback=traceback, unmet=unmet)
            return Checked(failed=True, traceback=traceback, unmet=unmet,
                           geometry=result.get("geometry") or {})

    if not acceptance:
        return None

    attempts = []
    for _ in range(budgets.attempts_per_step):
        if cancelled():
            raise Stopped()
        if attempts:
            request["your_failed_attempts_at_the_checks"] = list(attempts)

        supervisor = supervisor or SessionTreeSupervisor()
        task = json.dumps(request, indent=2, default=str)
        
        with copied(tools) as document, tempfile.TemporaryDirectory() as working_dir:
            declared, session, checkpoints = run_phase(
                pc.ASSERTION, working_dir,
                {"design": described},
                llm, task, document=document,
                kernel=kernel, supervisor=supervisor, stream=events,
                system=prompt(ASSERTION_PHASE)
            )
        
        reply = declared if isinstance(declared, dict) else {}
        code = str(reply.get("code") or "").strip()
        uncovered = [str(entry) for entry in (reply.get("uncovered") or ())]
        if not code:
            attempts.append({"code": "", "traceback": "You replied with no code. "
                                                      "Write the checks."})
            continue

        events.emit(TOOL_CALL, name="check_assertions", arguments={"code": code})
        result = tools.call("check_assertions", {"code": code})
        events.emit(TOOL_RESULT, name="check_assertions", result=result)

        if result.get("ok"):
            held = result.get("held")
            confirmed = list(held) if held is not None else \
                [entry for entry in acceptance if entry not in uncovered]
            checked = Checked(confirmed=confirmed, uncovered=uncovered,
                              geometry=result.get("geometry") or {})
            events.emit(ASSERTION, ok=True, code=code, confirmed=confirmed,
                        uncovered=uncovered, traceback="")
            return checked

        traceback = result.get("traceback") or "The checks failed without saying why."
        if _the_model_failed(traceback):
            held = result.get("held")
            confirmed = list(held) if held is not None else []
            worker_failed = result.get("failed")
            unmet = list(worker_failed) if worker_failed is not None else \
                (_unmet_in(traceback) or list(acceptance))
            events.emit(ASSERTION, ok=False, code=code, confirmed=confirmed,
                        uncovered=uncovered, traceback=traceback, unmet=unmet)
            return Checked(failed=True, confirmed=confirmed, traceback=traceback,
                           uncovered=uncovered, unmet=unmet,
                           geometry=result.get("geometry") or {})

        attempts.append({"code": code, "traceback": traceback})

    events.emit(ASSERTION, ok=None, code="", confirmed=[],
                uncovered=list(acceptance),
                traceback=attempts[-1]["traceback"] if attempts else "")
    return Checked(uncovered=list(acceptance))


def _unmet_in(traceback):
    """
    The acceptance entries a failed check named, one per entry.

    The fallback for a failure the worker's own `failed` list did not cover —
    the envelope check, which never builds one, and a check script that
    raised without following the assertion.md shape at all. Where a script
    did build `failed`, the caller reads that directly, off the same
    namespace `held` comes off; this is what is left for the traceback string
    that stands in for it, parsed rather than returned as data because that
    string is what a broken or older script leaves behind.

    The checks end in a single assert whose message lists every entry that did
    not hold, separated by semicolons — the Assertion prompt specifies that
    shape, so this reads it rather than guessing at one. A Document that does
    not recompute has no entry list at all: nothing was measured, so nothing
    can be named, and the caller falls back to the whole list.
    """
    for line in traceback.strip().splitlines():
        if _DID_NOT_HOLD not in line:
            continue
        listed = line.split(_DID_NOT_HOLD, 1)[1]
        return [entry.strip() for entry in listed.split(";") if entry.strip()]
    return []


def _the_model_failed(traceback):
    """
    Whether this traceback is about the part or about the code that checked it.

    Two ways the part is at fault. An `AssertionError` is an entry of the list
    that did not hold. A Document the copy cannot recompute is a part already
    broken before anything was measured — that one has to read differently in
    the feedback, or Plan is told a dimension is wrong when the model is in
    error.

    Any line rather than the last one, because the checks now end in a single
    assert naming every entry that failed, and a message listing forty of them
    is not going to stay on one line. A traceback's own frames are indented, so
    the only line that can begin with the exception name is the exception.
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
    Features whose geometry moved since the last Assertion pass that held,
    and are not one of the exact Names a retried Step actually touched.

    `core/apply.py` and Preflight track only the names a Step declared
    touching, never whether some other feature's geometry moved underneath a
    repair Step — `config/prompts/assertion.md` already documents the result:
    a slot shortened until it passes an acceptance check, a clamp that no
    longer opens. `retried_names` is the exact per-Step `created`/`modified`
    Names `apply_step` already measures (`core/apply.py`), carried out of
    `_work_through` for every Step an attempt retried — an exact match against
    FreeCAD `Object.Name`, not a substring against Acceptance citation prose:
    a prefix relationship between two Names ("Slot" of "SlotChamfer", or
    FreeCAD's own auto-numbered "Fillet" of "Fillet001") never excuses one for
    the other.

    `baseline` is a snapshot from the last Assertion pass that held (built by
    the caller, one entry per feature Volume and BoundBox). An empty baseline
    has nothing to compare against and reports nothing moved, which is the
    invariant that makes the very first Assertion pass of a run a no-op here.
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
