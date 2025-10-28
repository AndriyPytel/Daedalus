from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


class FreeCADScriptInput(BaseModel):
	"""Schema for FreeCAD Python execution tool."""
	script: str = Field(description="A syntactically correct multi-line Python script to execute in FreeCAD.")


class ExecuteFreeCADPythonTool(BaseTool):
	name = "execute_freecad_python"
	description = (
		"A tool to execute Python code in FreeCAD. Use this for any request that involves creating, "
		"modifying, or querying objects."
	)
	args_schema = FreeCADScriptInput

	def _run(self, script: str):
		# This tool is intercepted by the UI. Failsafe only.
		raise NotImplementedError(
			"This tool is not meant to be run directly. Its calls must be intercepted by the UI."
		)
