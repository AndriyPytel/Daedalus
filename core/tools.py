"""
The Tool registry — the only sanctioned route to the Document.

Tools are plain functions carrying a JSON Schema. Nothing here knows how a
Tool is called: `describe()` hands out transport-independent descriptions and
`as_openai_tools()` is one adapter over them. An MCP adapter is another
adapter, not a change to any Tool.

This module never imports FreeCAD. The FreeCAD-backed Tools live in
`core/freecad_tools.py`; a fake registry for Level 1 is just a few plain
functions handed to `ToolRegistry`.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List

NO_PARAMETERS = {"type": "object", "properties": {}}


class ToolError(Exception):
    """A Tool was called wrongly, or it failed. Goes back to the assistant."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    run: Callable
    parameters: dict = field(default_factory=lambda: dict(NO_PARAMETERS))

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    """
    A named set of Tools.

    Substitutable wholesale: Level 1 builds one over plain Python functions,
    the addon builds one over a live FreeCAD, and nothing above the registry
    can tell the difference.
    """

    def __init__(self, tools: Iterable[Tool] = ()):
        self._tools: Dict[str, Tool] = {}
        for tool in tools:
            self.add(tool)

    def add(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError("duplicate Tool: " + tool.name)
        self._tools[tool.name] = tool
        return tool

    def tool(self, name: str, description: str, parameters: dict = None):
        """Decorator form: register the function below as a Tool."""
        def register(func):
            self.add(Tool(name=name, description=description, run=func,
                          parameters=parameters or dict(NO_PARAMETERS)))
            return func
        return register

    def names(self) -> List[str]:
        return list(self._tools)

    def describe(self) -> List[dict]:
        """Transport-independent. Adapters are built on top of this, not instead."""
        return [tool.describe() for tool in self._tools.values()]

    def as_openai_tools(self) -> List[dict]:
        return [{"type": "function", "function": tool.describe()} for tool in self._tools.values()]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools.values())

    def call(self, name: str, arguments: dict = None) -> Any:
        """
        Invoke a Tool by name. Arguments come from the model, so they are
        checked against the schema before the Document hears about them.
        """
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(
                "No Tool named %r. Available: %s" % (name, ", ".join(sorted(self._tools)))
            )
        arguments = dict(arguments or {})
        _check(tool, arguments)
        try:
            return tool.run(**arguments)
        except ToolError:
            raise
        except Exception as exc:
            raise ToolError("%s failed: %s: %s" % (name, type(exc).__name__, exc))


def _check(tool: Tool, arguments: dict):
    schema = tool.parameters or NO_PARAMETERS
    allowed = set(schema.get("properties", {}))
    required = set(schema.get("required", []))

    missing = sorted(required - set(arguments))
    if missing:
        raise ToolError("%s is missing required argument(s): %s" % (tool.name, ", ".join(missing)))

    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise ToolError(
            "%s got unknown argument(s): %s. It takes: %s"
            % (tool.name, ", ".join(unknown), ", ".join(sorted(allowed)) or "nothing")
        )
