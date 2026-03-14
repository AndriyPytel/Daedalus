# FreeCadCopilot/agents/developer_agent.py
from langchain_core.prompts import ChatPromptTemplate
from .base_agent import BaseAgent

class DeveloperAgent(BaseAgent):
    """
    The Developer takes the Designer's strategy and writes FreeCAD Python API scripts.
    Outputs proper code that modifies App, Part, Sketcher, etc.
    """
    def __init__(self):
        super().__init__(role="Developer")
        self.prompt_template = ChatPromptTemplate.from_messages([
            ("system", self.system_prompt),
            ("human", "Write FreeCAD Python code for this strategy:\n{strategy}\nContext:\n{context}")
        ])

    def invoke(self, state: dict):
        if not self.llm: return {"error": "Developer LLM not initialized."}
        
        strategy = state.get("strategy", "")
        context = state.get("context", "")
        
        messages = self.prompt_template.format_messages(strategy=strategy, context=context)
        response = self.llm.invoke(messages)
        
        # Simple extraction assumes the LLM outputs markdown code blocks
        script = response.content
        if "```python" in script:
            script = script.split("```python")[1].split("```")[0].strip()
            
        return {
            "generated_code": script,
            "next_step": "review"
        }
