# FreeCadCopilot/agents/base_agent.py
import FreeCAD

def _get_llm_instance(provider, model_name, api_key):
    # Lazy imports to ensure dependency_manager can install before they are evaluated
    if provider == "OpenAI":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model_name, api_key=api_key, temperature=0)
    elif provider == "Google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model_name, google_api_key=api_key, temperature=0)
    elif provider == "Anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model_name=model_name, api_key=api_key, temperature=0)
    else:
        raise ValueError(f"Unsupported provider: {provider}")

class BaseAgent:
    """
    BaseAgent provides standard initialization for all specialized agents.
    It fetches LLM credentials from user preferences and sets up the LLM.
    """
    def __init__(self, role: str):
        from utils.config_manager import ConfigManager
        self.role = role
        
        try:
            self.provider, self.model_name, self.api_key = ConfigManager.get_llm_credentials()
            self.llm = _get_llm_instance(self.provider, self.model_name, self.api_key)
        except Exception as e:
            FreeCAD.Console.PrintError(f"[{role}] Failed to initialize LLM: {str(e)}\n")
            self.llm = None
            
        prefs = ConfigManager.get_preferences()
        
        # Determine specific prompt key based on role
        if role == "Orchestrator":
            self.system_prompt = prefs.get("orchestrator_prompt", prefs["global_system_prompt"])
        elif role == "Developer":
            self.system_prompt = prefs.get("developer_prompt", prefs["global_system_prompt"])
        elif role == "Designer":
            self.system_prompt = prefs.get("designer_prompt", prefs["global_system_prompt"])
        elif role == "Validator":
            self.system_prompt = prefs.get("validator_prompt", prefs["global_system_prompt"])
        elif role == "Reviewer":
            self.system_prompt = prefs.get("reviewer_prompt", prefs["global_system_prompt"])
        else:
            self.system_prompt = prefs.get("global_system_prompt", "You are a specialized AI assistant for FreeCAD.")

    def invoke(self, state: dict):
        raise NotImplementedError("Subclasses must implement the invoke() method.")
