"""
`by_label`'s "more than one match" branch never fires through the running
kernel -- FreeCAD renames a Label collision (`BasePad001`) rather than
letting two objects share one -- so it is exercised here against a fake
Document instead.
"""
import types

import pytest


def test_more_than_one_match_lists_every_label_it_found():
    from core.kernel.kernel_worker import by_label

    doc = types.SimpleNamespace(Objects=[
        types.SimpleNamespace(Label="BasePad"),
        types.SimpleNamespace(Label="BasePad"),
    ])

    with pytest.raises(LookupError) as excinfo:
        by_label(doc, "BasePad")

    assert "BasePad" in str(excinfo.value)
