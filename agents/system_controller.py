# FreeCadCopilot/agents/system_controller.py
from .orchestrator import OrchestratorAgent
from .context_injector import ContextInjector
from .designer_agent import DesignerAgent
from .developer_agent import DeveloperAgent
from .validator_agent import ValidatorAgent
from .reviewer_agent import ReviewerAgent

class MultiAgentController:
    """
    Manages the workflow between the Orchestrator, Designer, Developer, Validator, and Reviewer.
    It passes the shared state dict between agents.
    """
    def __init__(self):
        self.orchestrator = OrchestratorAgent()
        self.designer = DesignerAgent()
        self.developer = DeveloperAgent()
        self.validator = ValidatorAgent()
        self.reviewer = ReviewerAgent()

    def process_request(self, user_input: str):
        """
        Main entry point for processing an asynchronous UI request.
        """
        state = {
            "user_input": user_input,
            "next_step": "orchestrate",
            "history": [] 
        }

        # A simple state machine routing the task
        while state["next_step"] != "finish" and "error" not in state:
            current_step = state["next_step"]

            if current_step == "orchestrate":
                result = self.orchestrator.invoke(state)
                state.update(result)
                state["next_step"] = "design"
                
            elif current_step == "design":
                state["context"] = ContextInjector.get_designer_context(state)
                result = self.designer.invoke(state)
                state.update(result)
                state["next_step"] = "develop"

            elif current_step == "develop":
                state["context"] = ContextInjector.get_developer_context(state)
                result = self.developer.invoke(state)
                state.update(result)
                state["next_step"] = "validate"

            elif current_step == "validate":
                result = self.validator.invoke(state)
                if "error" in result:
                    state["error"] = result["error"]
                elif result.get("review_status") == "failed":
                    # Send back to Developer
                    state["feedback"] = result.get("feedback")
                    state["next_step"] = "develop"
                else:
                    state["next_step"] = "execute"
                    
            elif current_step == "execute":
                from core.command_executor import execute
                try:
                    execute(state.get("generated_code", ""))
                    state["next_step"] = "review"
                except Exception as e:
                    state["feedback"] = f"Execution failed with traceback:\n{str(e)}\nPlease fix the code."
                    state["next_step"] = "develop"

            elif current_step == "review":
                # The execution logic should be injected here or handled by the execution reviewer
                result = self.reviewer.invoke(state)
                state.update(result)
                state["next_step"] = "finish"
            
            else:
                state["error"] = f"Unknown step: {current_step}"
                break

        return state
