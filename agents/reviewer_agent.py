# FreeCadCopilot/agents/reviewer_agent.py
from langchain_core.prompts import ChatPromptTemplate
from .base_agent import BaseAgent

class ReviewerAgent(BaseAgent):
    """
    The Reviewer validates that the executed code matches the original design intent.
    It takes screenshots (Axo, Top, Front) and uses Vision to compare.
    """
    def __init__(self):
        super().__init__(role="Reviewer") # Reuses reviewer system prompt
        self.prompt_template = ChatPromptTemplate.from_messages([
            ("system", self.system_prompt),
            ("human", "Did this execution achieve the goal? Strategy: {strategy}. Code: {code}")
        ])

    def invoke(self, state: dict):
        if not self.llm: return {"error": "ExecutionReviewer LLM not initialized."}
        
        # Here we would use core.vision_utils to capture screenshots
        # For scaffolding, we mock the vision check
        
        strategy = state.get("strategy", "")
        code = state.get("generated_code", "")
        
        messages = self.prompt_template.format_messages(strategy=strategy, code=code)
        # response = self.llm.invoke(messages)
        
        # If execution fails the visual check, it can undo
        # from core.command_executor import revert
        # revert()
        # return {"execution_status": "failed", "next_step": "code"}
        
        # We will assume success for this scaffold
        return {
            "execution_status": "verified",
            "next_step": "finish"
        }
