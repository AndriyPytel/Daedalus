# FreeCadCopilot/agents/base_agent.py
"""
BaseAgent – shared initialisation and guarded invocation for all specialist agents.

Changes from previous version
------------------------------
- LLM init failure is reported with a full stack trace via log_exception().
- A safe_invoke() wrapper catches any LLM call exception, logs it, and returns
  a {"error": crash_report} dict so the caller can surface it in the UI.
"""
import FreeCAD
from utils.logger import log_exception, generate_crash_report


def _get_llm_instance(provider: str, model_name: str, api_key: str):
    if provider == "OpenAI":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model_name, api_key=api_key, temperature=0, streaming=True)
    elif provider == "Google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key,
                                      temperature=0, streaming=True)
    elif provider == "Anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model_name, api_key=api_key,
                             temperature=0, streaming=True)
    elif provider == "Custom/Local":
        from langchain_openai import ChatOpenAI
        # For local models, api_key often contains the base_url or is empty
        # We assume SettingsManager provides the base_url as the 'api_key' for this provider
        # or it's configured in a way that _get_llm_instance can use it.
        return ChatOpenAI(model=model_name, base_url=api_key, api_key="placeholder",
                          temperature=0, streaming=True)
    else:
        raise ValueError(f"Unsupported provider: {provider}")


class BaseAgent:
    """
    Provides standard init for all specialised agents.
    Fetches LLM credentials from SettingsManager and sets up the LLM.
    """

    def __init__(self, role: str, system_prompt_override=None):
        from utils.settings_manager import SettingsManager
        self.role = role
        self.llm  = None

        try:
            self.provider, self.model_name, self.api_key = SettingsManager.instance().get_llm_credentials()
            self.llm = _get_llm_instance(self.provider, self.model_name, self.api_key)
        except ValueError as v_err:
            # Silent handling for configuration errors (missing keys/models)
            from utils.logger import logger
            logger.warning(f"[{role}] LLM not configured: {v_err}. Please check API & Providers in Preferences.")
        except Exception as exc:
            log_exception(exc, context=f"[{role}] LLM initialisation")

        # System prompt priority:
        # 1. Explicit override passed by caller
        # 2. Per-role override stored in FreeCAD preferences (legacy)
        # 3. The PromptManager selection based on role
        # 4. Hardcoded global fallback
        if system_prompt_override:
            self.system_prompt = system_prompt_override
        else:
            from utils.config_manager import ConfigManager
            prefs = ConfigManager.get_preferences()
            role_map = {
                "Orchestrator": "orchestrator",
                "Developer":    "developer",
                "Designer":     "designer",
                "Validator":    "validator",
                "Reviewer":     "reviewer",
            }
            
            # 1. Check for specific user override in ParamGet (e.g. OrchestratorPrompt)
            # We use the role name to build the preference key
            pref_key = f"{role}Prompt"
            agent_override = prefs.get(pref_key.lower().replace("prompt", "_prompt"), "")
            if agent_override:
                self.system_prompt = agent_override
            else:
                # 2. Use PromptManager to find a matching prompt (factory or user)
                try:
                    from utils.prompt_manager import PromptManager
                    role_key = role_map.get(role, role.lower())
                    prompt_text = PromptManager.instance().get_prompt_text(role_key)
                    
                    if prompt_text:
                        self.system_prompt = prompt_text
                    else:
                        # 3. Last fallback: use the global prompt
                        self.system_prompt = prefs.get(
                            "global_system_prompt", "You are a specialised AI assistant for FreeCAD."
                        )
                except Exception:
                    self.system_prompt = prefs.get(
                        "global_system_prompt", "You are a specialised AI assistant for FreeCAD."
                    )

    # ── Guarded invocation ───────────────────────────────────────────────────

    def _refresh_llm_if_needed(self):
        from utils.settings_manager import SettingsManager
        try:
            provider, model_name, api_key = SettingsManager.instance().get_llm_credentials()
            if getattr(self, "provider", None) != provider or \
               getattr(self, "model_name", None) != model_name or \
               getattr(self, "api_key", None) != api_key:
                from utils.logger import logger
                logger.info(f"[{self.role}] Refreshing LLM instance for {model_name}")
                self.provider = provider
                self.model_name = model_name
                self.api_key = api_key
                self.llm = _get_llm_instance(self.provider, self.model_name, self.api_key)
        except Exception:
            pass

    def safe_invoke(self, state: dict) -> dict:
        """
        Calls self.invoke() and catches any exception, returning it as a
        structured crash report dict so the UI can display a friendly error.
        """
        self._refresh_llm_if_needed()
        if not self.llm:
            return {"error": f"[{self.role}] LLM not initialised. Check API keys in preferences."}
        try:
            return self.invoke(state)
        except Exception as exc:
            from utils.logger import is_transient_api_error
            if is_transient_api_error(exc):
                return {
                    "error": (
                        f"⚠️ **Service Unavailable or Quota Exceeded** ({self.provider})\n\n"
                        "The AI model is currently unavailable or your quota is exceeded. This usually happens when:\n"
                        "- The provider is experiencing an outage or maintenance.\n"
                        "- You've sent too many requests in a short time.\n"
                        "- Your API key has run out of credits or reached its monthly limit.\n\n"
                        "Please wait a few minutes and try again."
                    )
                }

            crash = generate_crash_report(exc, context=f"{self.role}.invoke()")
            return {"error": crash}

    def invoke(self, state: dict) -> dict:
        raise NotImplementedError("Subclasses must implement invoke().")
