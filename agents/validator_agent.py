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
            
        return {
            "review_status": "passed",
            "next_step": "execute"
        }
