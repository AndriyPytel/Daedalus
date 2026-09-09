"""
Level 1 — the Tool registry as a seam.

Everything here runs against a registry built from plain Python functions,
which is the point: the loop cannot tell that registry apart from the one
wired to a live FreeCAD.
"""
import pytest

from core.tools import NO_PARAMETERS, Tool, ToolError, ToolRegistry

ONE_NAME = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
}


@pytest.fixture
def fake():
    """The fake Tool registry every other Level 1 test will build on."""
    registry = ToolRegistry()
    state = {"objects": ["Box", "Pad"], "calls": []}

    @registry.tool("snapshot", "What exists right now.")
    def snapshot():
        state["calls"].append("snapshot")
        return {"objects": state["objects"], "selection": []}

    @registry.tool("describe_object", "One object's real properties.", ONE_NAME)
    def describe_object(name):
        state["calls"].append("describe_object")
        if name not in state["objects"]:
            raise ToolError("No object named %r." % name)
        return {"name": name, "properties": ["Length", "Width", "Height"]}

    registry.state = state
    return registry


def test_a_tool_is_a_plain_function_with_a_schema():
    def add(a, b):
        return a + b

    registry = ToolRegistry([Tool("add", "Adds.", add, {
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
    })])

    assert registry.call("add", {"a": 2, "b": 3}) == 5


def test_a_tool_with_no_parameters_still_has_a_schema(fake):
    described = {d["name"]: d for d in fake.describe()}

    assert described["snapshot"]["parameters"] == NO_PARAMETERS


def test_duplicate_tool_names_are_refused(fake):
    with pytest.raises(ValueError, match="duplicate Tool"):
        fake.add(Tool("snapshot", "Again.", lambda: None))


def test_descriptions_carry_no_transport_of_their_own(fake):
    described = fake.describe()

    assert [d["name"] for d in described] == ["snapshot", "describe_object"]
    for entry in described:
        assert set(entry) == {"name", "description", "parameters"}


def test_calling_a_tool_runs_the_function(fake):
    assert fake.call("snapshot")["objects"] == ["Box", "Pad"]
    assert fake.state["calls"] == ["snapshot"]


def test_an_unknown_tool_says_what_there_is(fake):
    with pytest.raises(ToolError) as raised:
        fake.call("delete_everything")

    assert "describe_object, snapshot" in str(raised.value)


def test_a_missing_required_argument_is_caught_before_the_document(fake):
    with pytest.raises(ToolError, match="missing required argument"):
        fake.call("describe_object", {})

    assert fake.state["calls"] == [], "the Tool ran anyway"


def test_an_argument_the_tool_does_not_take_is_caught_before_the_document(fake):
    with pytest.raises(ToolError) as raised:
        fake.call("describe_object", {"name": "Box", "colour": "red"})

    assert "unknown argument(s): colour" in str(raised.value)
    assert fake.state["calls"] == []


def test_a_tool_that_fails_reports_it_as_a_tool_error(fake):
    def explode():
        raise RuntimeError("the C++ core said no")

    fake.add(Tool("explode", "Fails.", explode))

    with pytest.raises(ToolError, match="explode failed: RuntimeError: the C\\+\\+ core said no"):
        fake.call("explode")


def test_a_tools_own_error_reaches_the_caller_unwrapped(fake):
    with pytest.raises(ToolError, match="No object named 'Ghost'"):
        fake.call("describe_object", {"name": "Ghost"})


def test_the_registry_is_introspectable_so_callers_need_no_import(fake):
    assert "snapshot" in fake
    assert len(fake) == 2
    assert sorted(fake.names()) == ["describe_object", "snapshot"]
    assert sorted(t.name for t in fake) == ["describe_object", "snapshot"]
