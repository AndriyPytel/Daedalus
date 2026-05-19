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
        callbacks = []
        stream_callback = state.get("stream_callback")
        if stream_callback:
            from agents.callbacks import UIStreamCallbackHandler
            callbacks.append(UIStreamCallbackHandler(stream_callback))
            
        response = self.llm.invoke(messages, config={"callbacks": callbacks})
        
        # Extract code and reasoning
        content = response.content
        reasoning = content
        script = ""
        
        if "```python" in content:
            parts = content.split("```python")
            reasoning = parts[0].strip()
            if len(parts) > 1:
                script = parts[1].split("```")[0].strip()
            
        return {
            "developer_reasoning": reasoning,
            "generated_code": script,
            "next_step": "validate"
        }
