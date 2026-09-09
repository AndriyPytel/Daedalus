"""
Level 1 -- ownership observed by a PropertiesList diff, not recorded by a
Tool call (FCC-300).

FCC-299 opened the kernel namespace, so a Step's code no longer has to route
through anything `core/kernel_worker.py` could intercept. `_Touched` answers
this by snapshotting the Document's own state before and after a Step's code
runs -- the same before/after `PropertiesList` diff `fcc-research-doc-diff.md`
demonstrated -- rather than by listening for a call it hooked. Three shapes,
matching the research's own test shapes: a new object, a changed property, an
object nothing touched.
"""
from core.kernel.kernel_worker import _Touched


class FakeObj:
    def __init__(self, name, **props):
        self.Name = name
        self._props = dict(props)

    @property
    def PropertiesList(self):
        return list(self._props)

    def __getattr__(self, item):
        try:
            return self._props[item]
        except KeyError:
            raise AttributeError(item)


class FakeDoc:
    def __init__(self, objects):
        self.Objects = list(objects)


def test_a_new_object_is_created_not_modified():
    before = FakeDoc([FakeObj("Box", Length=10)])
    touched = _Touched(before)

    after = FakeDoc([FakeObj("Box", Length=10), FakeObj("Pad")])
    touched.diff(after)

    assert touched.created == {"Pad"}
    assert touched.modified() == set()


def test_a_changed_property_is_modified_not_created():
    before = FakeDoc([FakeObj("Box", Length=10)])
    touched = _Touched(before)

    after = FakeDoc([FakeObj("Box", Length=20)])
    touched.diff(after)

    assert touched.created == set()
    assert touched.modified() == {"Box"}


def test_an_untouched_object_is_neither():
    before = FakeDoc([FakeObj("Box", Length=10)])
    touched = _Touched(before)

    after = FakeDoc([FakeObj("Box", Length=10)])
    touched.diff(after)

    assert touched.created == set()
    assert touched.deleted == set()
    assert touched.modified() == set()


def test_a_deleted_object_is_deleted_not_modified():
    before = FakeDoc([FakeObj("Box", Length=10), FakeObj("Pad")])
    touched = _Touched(before)

    after = FakeDoc([FakeObj("Box", Length=10)])
    touched.diff(after)

    assert touched.deleted == {"Pad"}
    assert touched.modified() == set()


def test_name_and_label_changes_are_excluded_from_the_diff():
    """The research's own exclusion rule: renaming is not a modification."""
    before = FakeDoc([FakeObj("Box", Length=10, Label="Box")])
    touched = _Touched(before)

    after = FakeDoc([FakeObj("Box", Length=10, Label="RenamedBox")])
    touched.diff(after)

    assert touched.modified() == set()


def test_a_computed_property_recompute_does_not_register_as_modified():
    """
    `Shape` is derived by recompute, not assigned by the Step -- the same
    false-positive `DERIVED` already guarded against under the signal-based
    mechanism this replaces.
    """
    before = FakeDoc([FakeObj("Pad", Shape="empty")])
    touched = _Touched(before)

    after = FakeDoc([FakeObj("Pad", Shape="a real solid")])
    touched.diff(after)

    assert touched.modified() == set()

