"""
The agent's namespace is FreeCAD's own Python, and it persists.

The epic's premise is that the wrapper needs no Jupyter kernel and no ZMQ hop:
a phase gets one in-process namespace, and what it builds in one turn is still
there in the next. Both halves of that are claims about FreeCAD's interpreter,
not about a mock, so this asserts them against a real Document.
"""
import pytest

pytestmark = pytest.mark.freecad


def test_the_namespace_carries_a_real_document_from_one_turn_to_the_next():
    import FreeCAD

    from core.agent import Namespace

    namespace = Namespace()
    namespace.run("import FreeCAD; document = FreeCAD.newDocument('agent_namespace')")
    namespace.run("box = document.addObject('Part::Box', 'B'); box.Length = 30")

    try:
        assert namespace.run("print(document.Name, box.Length.Value)").split() == [
            "agent_namespace",
            "30.0",
        ]
    finally:
        FreeCAD.closeDocument("agent_namespace")


def test_the_wrapper_reaches_freecad_without_a_kernel():
    import sys

    import core.agent

    for module in ("jupyter_client", "ipykernel", "IPython", "zmq", "nano_rlm"):
        assert module not in sys.modules, (
            "%s was imported: the wrapper is meant to run in FreeCAD's own "
            "interpreter, with no kernel and no socket" % module
        )
