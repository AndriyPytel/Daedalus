"""
`step_namespace()` carries the Design model into a Step's own namespace,
read-only, alongside the Document, never through the working directory.
"""
import sys
import types

import pytest


@pytest.fixture
def fake_freecad(monkeypatch):
    """A `FreeCAD` module stub so `step_namespace()` needs no real FreeCAD."""
    fake = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "FreeCAD", fake)
    return fake


def test_no_design_leaves_the_namespace_without_one(fake_freecad):
    from core.kernel.kernel_worker import step_namespace

    namespace = step_namespace(doc=object())

    assert "design" not in namespace


def test_design_is_carried_into_the_namespace(fake_freecad):
    from core.kernel.kernel_worker import step_namespace

    model = {"features": [{"about": "Boss", "length": 30.0}]}
    namespace = step_namespace(doc=object(), design=model)

    assert namespace["design"]["features"][0]["length"] == 30.0


def test_design_in_the_namespace_cannot_be_written_back(fake_freecad):
    from core.kernel.kernel_worker import step_namespace

    model = {"features": [{"about": "Boss", "length": 30.0}]}
    namespace = step_namespace(doc=object(), design=model)

    with pytest.raises(TypeError):
        namespace["design"]["features"] = []

    with pytest.raises(TypeError):
        namespace["design"]["features"][0]["length"] = 999.0

    with pytest.raises((TypeError, AttributeError)):
        namespace["design"]["features"].append({})


def test_design_read_only_leaves_the_caller_s_model_untouched(fake_freecad):
    """
    Read-only is a property of the namespace, not a mutation of the model
    Design produced -- the same dict is handed to Preflight, Apply and a
    later replay alike.
    """
    from core.kernel.kernel_worker import step_namespace

    model = {"features": [{"about": "Boss", "length": 30.0}]}
    step_namespace(doc=object(), design=model)

    assert model["features"][0]["length"] == 30.0
    model["features"][0]["length"] = 999.0
    assert model["features"][0]["length"] == 999.0
