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
        
        # Instruct the Orchestrator to output natural text followed by routing tags
        # Instruct the Orchestrator to output natural text followed by routing tags
        self.prompt_template = ChatPromptTemplate.from_messages([
            ("system", self.system_prompt + "\n\n"
             "You are the Orchestrator. Analyze the user request and decide the next step.\n"
             "Explain your reasoning naturally in markdown.\n"
             "At the very end of your response, you MUST include these exact routing tags using markdown blockquotes:\n"
             "> **NEXT_AGENT:** one of design/develop/validate/review/execute/wait\n"
             "> **AGENT_PROMPT:** Specific instructions for the chosen agent (leave empty if wait)\n"
             "If you are waiting for user input, just use > **NEXT_AGENT:** wait.\n"
            ),
            ("human", "User Request: {user_input}\nContext: {context}")
        ])
        
    def invoke(self, state: dict):
        """
        Receives standard state dict. Returns parsed components.
        """
        if not self.llm:
            return {"error": "Orchestrator LLM not initialized. Check API keys."}
            
        user_input = state.get("user_input", "")
        # Get history to provide better context to the orchestrator
        history = state.get("history", [])
        history_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in history[-5:]])
        context = state.get("context", "")
        if history_text:
            context = f"Recent History:\n{history_text}\n\nAdditional Context:\n{context}"
        elif not context:
            context = "No additional context."
            
        messages = self.prompt_template.format_messages(
            user_input=user_input, 
            context=context
        )
        
        callbacks = []
        stream_callback = state.get("stream_callback")
        if stream_callback:
            from agents.callbacks import UIStreamCallbackHandler
            # Make sure it streams to the UI
            callbacks.append(UIStreamCallbackHandler(stream_callback))
        
        response = self.llm.invoke(messages, config={"callbacks": callbacks})
        content = response.content.strip()
        
        next_step = "wait"
        agent_prompt = ""
        orchestrator_output = content
        
        # Parse the output tags
        lines = content.split("\n")
        for line in lines:
            if "> **NEXT_AGENT:**" in line:
                next_step = line.split("> **NEXT_AGENT:**")[-1].strip().lower()
            elif "> **AGENT_PROMPT:**" in line:
                agent_prompt = line.split("> **AGENT_PROMPT:**")[-1].strip()
                
        orchestrator_output = content
        
        return {
            "strategy": orchestrator_output, # Still return strategy for downstream agents if needed
            "next_step": next_step,
            "agent_prompt": agent_prompt,
            "orchestrator_output": orchestrator_output,
        }
