"""
Level 2 -- the kernel namespace is open.

`ToolRegistry` stops being the only door to the Document: a Step's code runs
directly against the live Document and FreeCAD's own modules, with a Tool
function from `core/document_tools.py` still reachable as a convenience, not
as the sole entry point.
"""
import pytest

from core.kernel import Kernel, find_freecadcmd

pytestmark = pytest.mark.freecad


@pytest.fixture
def kernel():
    try:
        find_freecadcmd()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    with Kernel() as running:
        yield running


def test_a_step_can_call_freecad_directly_with_no_tool_involved(kernel):
    """AC1: no registered Tool is called here at all, only FreeCAD's own API."""
    result = kernel.rehearse("doc.addObject('Part::Box', 'Box')")

    assert result.ok, result.traceback
    assert "Box" in result.created


def test_a_freecad_tools_helper_still_works_from_a_steps_code(kernel):
    """AC2: an existing convenience Tool function is not removed, only optional."""
    result = kernel.rehearse(
        "from core.document_tools import description\n"
        "doc.addObject('Part::Box', 'Box')\n"
        "doc.recompute()\n"
        "assert description(doc)['features'][0]['name'] == 'Box'"
    )

    assert result.ok, result.traceback


def test_a_bare_import_app_resolves_to_the_pre_bound_freecad_module(kernel):
    """A bare `import App` must not shadow the pre-bound name with FreeCAD's
    own `Mod/Mesh/App` namespace package (dcaa993's now-fixed trap)."""
    result = kernel.rehearse(
        "import App\n"
        "assert App is FreeCAD\n"
        "App.Vector(1, 2, 3)"
    )

    assert result.ok, result.traceback


def test_a_step_sees_the_ten_helpers_plus_its_document_and_its_name_map(kernel):
    result = kernel.probe(
        "import types\n"
        "print(sorted(k for k, v in globals().items()\n"
        "             if not k.startswith('__') and not isinstance(v, types.ModuleType)))"
    )

    assert result.ok, result.traceback
    assert eval(result.stdout.strip()) == sorted([
        "bbox", "built_ok", "by_label", "cylinder_face", "doc", "edges_between", "edges_by_concavity",
        "names", "pick_edges", "pick_faces", "plane_face", "sketch_at"])
