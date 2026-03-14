# FreeCadCopilot/agents/orchestrator.py
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage

from .base_agent import BaseAgent

class OrchestratorAgent(BaseAgent):
    """
    The Orchestrator acts as the "brain".
    It takes user input, breaks it into a task list, and delegates to other agents.
    """
    def __init__(self):
        super().__init__(role="Orchestrator")
        
        # In a concrete implementation, this prompt instructs the Orchestrator to define subtasks
        # e.g., ["Search API", "Plan Design", "Generate Code", "Review"]
        self.prompt_template = ChatPromptTemplate.from_messages([
            ("system", self.system_prompt),
            ("human", "User Request: {user_input}\nContext: {context}")
        ])
        
    def invoke(self, state: dict):
        """
        Receives standard state dict (containing user_input, conversation history).
        Outputs a planned set of steps or next action.
        """
        if not self.llm:
            return {"error": "Orchestrator LLM not initialized. Check API keys."}
            
        user_input = state.get("user_input", "")
        context = state.get("context", "No additional context.")
        
        messages = self.prompt_template.format_messages(
            user_input=user_input, 
            context=context
        )
        
        response = self.llm.invoke(messages)
        return {
            "orchestrator_output": response.content,
            "next_step": "plan" # Hardcoded for now, would be dynamically generated
        }
