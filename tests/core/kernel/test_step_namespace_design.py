"""
`step_namespace()` carries the Design model into a Step's own namespace, a
private `copy.deepcopy` alongside the Document, never through the working
directory.
"""
import json
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


def test_design_is_a_plain_dict_with_features_keyed_by_id(fake_freecad):
    from core.kernel.kernel_worker import step_namespace

    model = {"features": [{"id": "Boss", "length": 30.0}]}
    namespace = step_namespace(doc=object(), design=model)
    design = namespace["design"]

    assert isinstance(design, dict)
    assert json.dumps(design)
    assert design["features"]["Boss"]["length"] == 30.0


def test_lint_message_example_path_runs_against_a_real_design(fake_freecad):
    """The dimensional-literal lint message points at `design['features']['Boss']['length']`;
    that path has to resolve against what a Step actually sees, not raise."""
    from core.kernel.kernel_worker import step_namespace

    model = {"features": [{"id": "Boss", "length": 30.0}]}
    namespace = step_namespace(doc=object(), design=model)
    design = namespace["design"]

    assert design['features']['Boss']['length'] == 30.0


def test_writing_into_the_namespace_design_does_not_reach_the_caller_s_model(fake_freecad):
    """A Step's own copy, so a write into it changes only that copy -- nothing
    reads it back, and the model Design produced is unaffected."""
    from core.kernel.kernel_worker import step_namespace

    model = {"features": [{"id": "Boss", "length": 30.0}]}
    namespace = step_namespace(doc=object(), design=model)

    namespace["design"]["features"]["Boss"]["length"] = 999.0

    assert model["features"][0]["length"] == 30.0
