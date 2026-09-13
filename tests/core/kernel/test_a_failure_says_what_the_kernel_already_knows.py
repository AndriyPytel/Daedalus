"""
Level 1 -- a failed Step carries what the kernel already measured.

A Step's own code asserts its own volume, and that assert raises before any of
`_run_step_body`'s checks run. So the kernel's volume-neutral diagnosis --
which names a wrong `Reversed` outright -- never reached the model on the one
failure it was written for, and `introspect` answered a dereference of `None`
with nothing at all, because its guard cannot tell "no object information on
the exception" from "the object is None".

The two numbers this pins, read off the five kernel sessions of
2026-09-10 (logs/review/rlm-execute-cost-audit.md): 15 of 45 kernel failures
are the model's own volume assert, 10 of 45 are a dereference of a lookup that
returned None. One recorded Pocket took 35 kernel calls to find the sign of
its own direction by hand.

No FreeCAD: every check here reads volumes and PropertiesList off objects,
and a fake carries both.
"""
from core.agent.agent import _feedback
from core.kernel.kernel import KernelResult
from core.kernel.kernel_worker import (_idle_feature_report, _volume_moved,
                                       introspect, run_step)


class FakeShape:
    def __init__(self, volume):
        self.Volume = volume
        self.Solids = [object()]

    def isNull(self):
        return False


class FakeFeature:
    """A PartDesign feature whose volume depends on which way it cuts."""

    def __init__(self, name, type_id, base=None, volume=0.0, flipped=None):
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self.BaseFeature = base
        self.Reversed = False
        self._volume = volume
        self._flipped = volume if flipped is None else flipped
        self.Document = None

    @property
    def Shape(self):
        return FakeShape(self._flipped if self.Reversed else self._volume)

    def isDerivedFrom(self, type_id):
        return type_id == "PartDesign::Feature"


class FakeDoc:
    def __init__(self, *objects):
        self.Objects = list(objects)
        for obj in objects:
            obj.Document = self

    def getObject(self, name):
        return next((o for o in self.Objects if o.Name == name), None)

    def recompute(self, scope=None):
        return 0


def _pocket_that_cut_nothing():
    pad = FakeFeature("Pad", "PartDesign::Pad", volume=1000.0, flipped=1000.0)
    pocket = FakeFeature("Pocket", "PartDesign::Pocket", base=pad,
                         volume=1000.0, flipped=750.0)
    return FakeDoc(pad, pocket)


def test_a_volume_neutral_feature_names_its_cause_in_the_report():
    report = _idle_feature_report(_pocket_that_cut_nothing(), ["Pocket"])

    assert "Pocket (PartDesign::Pocket)" in report
    assert "1000.0 mm3 before, 1000.0 mm3 after" in report
    assert "Set Reversed=True" in report


def test_a_feature_that_moved_material_reports_nothing():
    pad = FakeFeature("Pad", "PartDesign::Pad", volume=1000.0)
    pocket = FakeFeature("Pocket", "PartDesign::Pocket", base=pad, volume=750.0)

    assert _idle_feature_report(FakeDoc(pad, pocket), ["Pocket"]) == ""


def test_the_report_is_never_worth_the_traceback_it_explains():
    class Exploding(FakeDoc):
        def getObject(self, name):
            raise RuntimeError("the Document is gone")

    assert _idle_feature_report(Exploding(), ["Pocket"]) == ""


def test_a_failed_step_is_told_what_its_idle_feature_did():
    called = set(run_step.__code__.co_names)

    assert "_idle_feature_report" in called


def test_what_each_feature_moved_is_measured_for_the_model():
    pad = FakeFeature("Pad", "PartDesign::Pad", volume=1000.0)
    pocket = FakeFeature("Pocket", "PartDesign::Pocket", base=pad, volume=750.0)

    moved = _volume_moved(FakeDoc(pad, pocket), ["Pad", "Pocket"])

    assert moved == {"Pad": {"before": 0.0, "after": 1000.0, "moved": 1000.0},
                     "Pocket": {"before": 1000.0, "after": 750.0, "moved": -250.0}}


def _dereferenced_none():
    try:
        None.Shape
    except AttributeError as exc:
        return exc
    raise AssertionError("dereferencing None did not raise")


def test_a_dereference_of_none_is_answered_with_the_objects_that_exist():
    doc = _pocket_that_cut_nothing()

    hint = introspect(_dereferenced_none(), doc)

    assert "returned None" in hint
    assert "Pad (Pad, PartDesign::Pad)" in hint
    assert "Pocket (Pocket, PartDesign::Pocket)" in hint


def test_a_dereference_of_none_with_no_document_to_hand_says_nothing():
    assert introspect(_dereferenced_none()) == ""


def test_a_missing_property_on_a_real_object_still_lists_what_it_has():
    class Feature:
        TypeId = "PartDesign::Pad"
        PropertiesList = ["Length", "Reversed", "Type"]
        Length = 10.0
        Reversed = False
        Type = "Length"

    try:
        Feature().Lenght
    except AttributeError as exc:
        hint = introspect(exc, _pocket_that_cut_nothing())

    assert "PartDesign::Pad has no 'Lenght'" in hint
    assert "Closest existing: Length" in hint


def test_the_wire_carries_what_each_feature_moved_on_both_branches():
    moved = {"Pocket": {"before": 1000.0, "after": 750.0, "moved": -250.0}}

    landed = KernelResult.from_response({"ok": True, "volume": moved})
    refused = KernelResult.from_response({"ok": False, "traceback": "boom", "volume": moved})

    assert (landed.volume, refused.volume) == (moved, moved)


def test_a_step_that_failed_is_told_what_it_moved_and_what_exists():
    moved = {"Pocket": {"before": 1000.0, "after": 1000.0, "moved": 0.0}}
    refused = KernelResult.from_response({"ok": False, "traceback": "boom", "volume": moved})

    answer = _feedback(refused)

    assert answer["volume"] == moved
    assert answer["status"] == "failed"
