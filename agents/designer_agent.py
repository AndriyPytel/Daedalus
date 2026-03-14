# FreeCadCopilot/agents/designer_agent.py
from langchain_core.prompts import ChatPromptTemplate
from .base_agent import BaseAgent

class DesignerAgent(BaseAgent):
    """
    The Designer determines the parametric strategy.
    It takes the Orchestrator's plan and designs the mathematical geometric constraints.
    """
    def __init__(self):
        super().__init__(role="Designer")
        self.prompt_template = ChatPromptTemplate.from_messages([
            ("system", self.system_prompt),
            ("human", "Design this component: {task_details}\nContext: {context}")
        ])

    def invoke(self, state: dict):
        if not self.llm: return {"error": "Designer LLM not initialized."}
        
        task_details = state.get("task_details", "")
        context = state.get("context", "")
        
        messages = self.prompt_template.format_messages(task_details=task_details, context=context)
        response = self.llm.invoke(messages)
        
        return {
            "strategy": response.content,
            "next_step": "code"
        }
