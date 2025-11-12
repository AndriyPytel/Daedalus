# FreeCadCopilot/core/freecad_tool.py
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type

class FreeCADScriptInput(BaseModel):
    script: str = Field(description="The Python script to execute in FreeCAD.")

class ExecuteFreeCADPythonTool(BaseTool):
    name: str = "execute_freecad_python"
    description: str = "Executes a Python script in FreeCAD to perform an action."
    args_schema: Type[BaseModel] = FreeCADScriptInput

    def _run(self, script: str):
        raise NotImplementedError("This tool is not meant to be run directly. It should be intercepted.")
