# FreeCadCopilot/ai_handlers/AgentFactory.py
import FreeCAD
import json
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from langchain.agents import create_agent
from langchain_core.prompts import ChatPromptTemplate
from core.freecad_tool import ExecuteFreeCADPythonTool

def create_agent_executor():
    """
    Creates and returns a runnable agent.
    Note: Despite the name, this returns a runnable agent, not an AgentExecutor class instance.
    """
    param_grp = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/FreeCadCopilot")
    selected_model = param_grp.GetString("SelectedModel", "")
    api_keys_json = param_grp.GetString("ApiKeys", "[]")
    api_keys = json.loads(api_keys_json)
    global_system_prompt = param_grp.GetString("GlobalSystemPrompt", "You are a helpful assistant that creates Python scripts for FreeCAD.")

    if not selected_model or not api_keys:
        # This should be handled gracefully in the UI, but raise an error for now.
        raise ValueError("Model or API keys not configured in FreeCAD preferences.")

    # Find the correct API key for the selected model provider
    provider = ""
    api_key = ""
    # This logic assumes model names hint at the provider. A more robust mapping might be needed.
    for key_info in api_keys:
        provider_name = key_info.get("provider", "").lower()
        model_name_lower = selected_model.lower()
        if ("gpt" in model_name_lower and "openai" in provider_name):
            provider = "OpenAI"
            api_key = key_info.get("api_key")
            break
        elif ("gemini" in model_name_lower and "google" in provider_name):
            provider = "Google"
            api_key = key_info.get("api_key")
            break
        elif ("claude" in model_name_lower and "anthropic" in provider_name):
            provider = "Anthropic"
            api_key = key_info.get("api_key")
            break

    if not provider or not api_key:
        raise ValueError(f"Could not find a valid API key for the selected model: {selected_model}")

    # Instantiate the correct chat model
    if provider == "OpenAI":
        chat_model = ChatOpenAI(model_name=selected_model, api_key=api_key)
    elif provider == "Google":
        chat_model = ChatGoogleGenerativeAI(model=selected_model, google_api_key=api_key)
    elif provider == "Anthropic":
        chat_model = ChatAnthropic(model_name=selected_model, api_key=api_key)
    else:
        # This case should ideally not be reached if the above logic is sound.
        raise ValueError(f"Unsupported provider or model misconfiguration for: {selected_model}")

    tools = [ExecuteFreeCADPythonTool()]
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", global_system_prompt),
        ("user", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])
    
    # Create the agent, which is a runnable object.
    agent = create_agent(chat_model, tools, prompt)
    
    # Return the runnable agent directly. The .invoke() method will be called on this object.
    return agent
