# FreeCadCopilot/ai_handlers/AgentFactory.py
import FreeCAD
import json
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from langchain.agents import create_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from core.freecad_tool import ExecuteFreeCADPythonTool

# Static model lists for each provider
MODEL_MAP = {
    "OpenAI": ["gpt-4o", "gpt-4", "gpt-3.5-turbo"],
    "Google": ["models/gemini-1.5-pro-latest", "gemini-2.5-pro", "gemini-2.5-flash"],
    "Anthropic": ["claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307"],
    "Custom/Local": ["Custom/Local Model"]
}

def get_available_models():
    """Returns a list of all available models from all providers."""
    all_models = []
    for provider, models in MODEL_MAP.items():
        all_models.extend(models)
    return sorted(all_models)

def create_agent_executor():
    """
    Creates and returns a runnable agent.
    Note: Despite the name, this returns a runnable agent, not an AgentExecutor class instance.
    """
    param_grp = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/FreeCadCopilot")
    selected_model = param_grp.GetString("SelectedModel", "")
    api_keys_json = param_grp.GetString("ApiKeys", "[]")
    api_keys = json.loads(api_keys_json)
    system_prompt = param_grp.GetString("GlobalSystemPrompt", "You are a helpful assistant that creates Python scripts for FreeCAD.")

    if not selected_model or not api_keys:
        # This should be handled gracefully in the UI, but raise an error for now.
        raise ValueError("Model or API keys not configured in FreeCAD preferences.")

    # Find the correct API key for the selected model provider
    provider = ""
    api_key = ""
    # This logic assumes model names hint at the provider. A more robust mapping might be needed.
    for key_info in api_keys:
        provider_name = key_info.get("provider", "")

        if provider_name in MODEL_MAP and selected_model in MODEL_MAP[provider_name]:
            provider = provider_name
            api_key = key_info.get("api_key")
            break

    if not provider or not api_key:
        raise ValueError(f"Could not find a valid API key for the selected model: {selected_model}")

    # Instantiate the correct chat model
    if provider == "OpenAI":
        chat_model = ChatOpenAI(model=selected_model, api_key=api_key)
    elif provider == "Google":
        chat_model = ChatGoogleGenerativeAI(model=selected_model, google_api_key=api_key)
    elif provider == "Anthropic":
        chat_model = ChatAnthropic(model_name=selected_model, api_key=api_key)
    elif provider == "Custom/Local":
        # Custom model instantiation logic would go here.
        # This might involve using a different LangChain class and passing an API endpoint.
        # For now, this will raise an error if a custom model is selected without further implementation.
        raise NotImplementedError("Custom/Local models are not yet fully implemented in AgentFactory.")
    else:
        # This case should ideally not be reached if the above logic is sound.
        raise ValueError(f"Unsupported provider or model misconfiguration for: {selected_model}")

    tools = [ExecuteFreeCADPythonTool()]

    agent = create_agent(chat_model, tools, system_prompt=system_prompt)

    # Return the runnable agent directly. The .invoke() method will be called on this object.
    return agent
