# FreeCadCopilot/agents/system_controller.py
"""
MultiAgentController – orchestrates the full agent pipeline.

Changes from previous version
------------------------------
- process_request() accepts an optional step_callback(step_name, step_idx, total)
  so the UI status line can update live.
- All agent calls go through safe_invoke() for structured error propagation.
- Top-level try/except ensures uncaught errors produce a crash report.
"""
from .orchestrator    import OrchestratorAgent
from .context_injector import ContextInjector
from .designer_agent  import DesignerAgent
from .developer_agent import DeveloperAgent
from .validator_agent import ValidatorAgent
from .reviewer_agent  import ReviewerAgent
from utils.logger     import generate_crash_report, log_exception

# Ordered list used for progress reporting
_STEPS = ["Orchestrating", "Designing", "Developing", "Validating", "Reviewing"]
_TOTAL = len(_STEPS)


class MultiAgentController:
    """
    Routes the shared state dict through the full agent pipeline.
    """

    def __init__(self):
        self.orchestrator = OrchestratorAgent()
        self.designer     = DesignerAgent()
        self.developer    = DeveloperAgent()
        self.validator    = ValidatorAgent()
        self.reviewer     = ReviewerAgent()

    # ── Main entry point ─────────────────────────────────────────────────────

    def process_request(
        self,
        user_input: str,
        stream_callback=None,
        step_callback=None,
    ) -> dict:
        """
        Parameters
        ----------
        user_input      : raw user message
        stream_callback : callable(token: str) – forwarded to LLM for streaming
        step_callback   : callable(step_name: str, step_idx: int, total: int)
                          called just before each agent runs (1-indexed)

        Returns the final state dict, always containing either
        "generated_code"/"orchestrator_output" or "error".
        """
        def _notify(step_name: str, idx: int):
            if callable(step_callback):
                try:
                    step_callback(step_name, idx, _TOTAL)
                except Exception:
                    pass  # never let UI callbacks crash the pipeline

        state = {
            "user_input":      user_input,
            "next_step":       "orchestrate",
            "history":         [],
            "stream_callback": stream_callback,
        }

        try:
            while state["next_step"] != "finish" and "error" not in state:
                current = state["next_step"]

                if current == "orchestrate":
                    _notify("Orchestrating", 1)
                    result = self.orchestrator.safe_invoke(state)
                    state.update(result)
                    
                    next_step = result.get("next_step", "wait")
                    agent_prompt = result.get("agent_prompt", state.get("user_input", ""))
                    
                    # Distribute prompt based on chosen agent
                    if next_step == "design":
                        state["task_details"] = agent_prompt
                    elif next_step == "develop":
                        state["strategy"] = agent_prompt
                    elif next_step == "wait":
                        state["next_step"] = "finish"
                        continue
                        
                    state["next_step"] = next_step

                elif current == "design":
                    _notify("Designing", 2)
                    state["context"] = ContextInjector.get_designer_context(state)
                    result = self.designer.safe_invoke(state)
                    state.update(result)
                    state["next_step"] = "develop"

                elif current == "develop":
                    _notify("Developing", 3)
                    state["context"] = ContextInjector.get_developer_context(state)
                    result = self.developer.safe_invoke(state)
                    state.update(result)
                    state["next_step"] = result.get("next_step", "finish")

                elif current == "validate":
                    _notify("Validating", 4)
                    result = self.validator.safe_invoke(state)
                    if "error" in result:
                        state["error"] = result["error"]
                    elif result.get("review_status") == "failed":
                        state["feedback"] = result.get("feedback")
                        state["next_step"] = "develop"   # retry
                    else:
                        state["next_step"] = "finish"

                elif current == "execute":
                    from core.command_executor import execute
                    exec_result = execute(state.get("generated_code", ""))
                    if exec_result["status"] == "error":
                        state["feedback"] = (
                            f"Execution failed:\n{exec_result['message']}\n"
                            "Please fix the code."
                        )
                        state["next_step"] = "develop"
                    else:
                        state["next_step"] = "review"

                elif current == "review":
                    _notify("Reviewing", 5)
                    result = self.reviewer.safe_invoke(state)
                    state.update(result)
                    state["next_step"] = "finish"

                else:
                    state["error"] = f"Unknown pipeline step: {current}"
                    break

        except Exception as exc:
            crash = generate_crash_report(exc, context="MultiAgentController.process_request()")
            state["error"] = crash

        return state
