try:
	from langchain_core.tools import BaseTool
except ImportError as e:
	print("FreeCadCopilot Error: Failed to import vendored libraries from 'lib'.")
	print(f"Missing module: {getattr(e, 'name', str(e))}")
	print("Please ensure all dependencies were correctly installed into the 'lib' folder.")
	BaseTool = object  # fallback to allow module import; usage will fail meaningfully later

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
