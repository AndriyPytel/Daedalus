# FreeCadCopilot/agents/validator_agent.py
import ast
from .base_agent import BaseAgent

class ValidatorAgent(BaseAgent):
    """
    The Validator Agent performs static analysis and linting before execution.
    It does not necessarily need an LLM for pure AST checks, but uses it if logical review is needed.
    """
    def __init__(self):
        super().__init__(role="Validator")

    def invoke(self, state: dict):
        generated_code = state.get("generated_code", "")
        if not generated_code:
            return {"error": "No code provided for review."}
            
        # Basic Safety Check via AST
        try:
            tree = ast.parse(generated_code)
            
            # Simple static analysis: look for forbidden calls. 
            # E.g., importing generic os/sys can be risky if not controlled, 
            # but for FreeCAD it might be allowed. Left as example for safety logic.
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) or isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name in ['subprocess', 'os'] and 'os' not in generated_code: # naive check
                            pass # Just an example concept

        except SyntaxError as e:
            return {
                "review_status": "failed",
                "feedback": f"Syntax Error in generated code: {str(e)}",
                "next_step": "code" # Send back to Coder
            }
            
        # FreeCAD 1.0 specific API validations
        if ".Support =" in generated_code or ".Support" in generated_code:
            return {
                "review_status": "failed",
                "feedback": "CRITICAL ERROR: 'sketch.Support' is DEPRECATED and removed in FreeCAD 1.0! Use 'sketch.Placement' instead.",
                "next_step": "code"
            }
            
        if ".Plane =" in generated_code or ".Plane" in generated_code:
            return {
                "review_status": "failed",
                "feedback": "CRITICAL ERROR: 'sketch.Plane' is DEPRECATED and removed in FreeCAD 1.0. You MUST assign to 'sketch.Placement' instead! (e.g. sketch.Placement = App.Placement(...))",
                "next_step": "code"
            }
            
        for line in generated_code.split('\n'):
            line = line.strip()
            if line.startswith("doc.") and "=" in line and not line.startswith("doc.Name") and not line.startswith("doc.Label"):
                return {
                    "review_status": "failed",
                    "feedback": f"CRITICAL ERROR: In line '{line}', you illegally assigned a dynamic attribute directly to App.Document! FreeCAD documents do not support arbitrary attributes like doc.Length. Use standard Python variables instead.",
                    "next_step": "code"
                }

        return {
            "review_status": "passed",
            "next_step": "execute"
        }
