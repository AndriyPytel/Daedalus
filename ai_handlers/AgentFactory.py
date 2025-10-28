import json
from typing import Optional, Dict, Any

try:
	import FreeCAD as App
except Exception:
	App = None

from langchain_openai import ChatOpenAI
try:
	from langchain_google_genai import ChatGoogleGenerativeAI
except Exception:
	ChatGoogleGenerativeAI = None

try:
	from langchain_anthropic import ChatAnthropic
except Exception:
	ChatAnthropic = None

from langchain_core.prompts import ChatPromptTemplate
from langchain.agents import create_tool_calling_agent, AgentExecutor

from core.freecad_tool import ExecuteFreeCADPythonTool


def _get_params():
	if App is None:
		return None
	return App.ParamGet("User parameter:BaseApp/Preferences/FreeCadCopilot")


def _load_preferences() -> Dict[str, Any]:
	params = _get_params()
	if not params:
		return {"api_keys": [], "selected_model": "", "system_prompt": ""}
	api_keys_json = params.GetString("ApiKeys", "")
	selected_model = params.GetString("SelectedModel", "")
	system_prompt = params.GetString("GlobalSystemPrompt", "")
	try:
		api_keys = json.loads(api_keys_json) if api_keys_json else []
	except Exception:
		api_keys = []
	return {"api_keys": api_keys, "selected_model": selected_model, "system_prompt": system_prompt}


def _find_provider_for_model(model_name: str) -> Optional[str]:
	from Mod.CopilotPreferences import CopilotPreferences
	for provider, models in CopilotPreferences.MODEL_MAP.items():
		if model_name in models:
			return provider
	if model_name == "Custom/Local Model":
		return "Custom/Local"
	return None


def _get_provider_credentials(api_keys: list, provider: str) -> Dict[str, Optional[str]]:
	for entry in api_keys:
		if entry.get("provider") == provider:
			return {"api_key": entry.get("key"), "endpoint": entry.get("endpoint")}
	return {"api_key": None, "endpoint": None}


def _instantiate_model(provider: str, model_name: str, creds: Dict[str, Optional[str]]):
	api_key = creds.get("api_key")
	endpoint = creds.get("endpoint")
	if provider == "OpenAI":
		return ChatOpenAI(model=model_name, api_key=api_key)
	elif provider == "Google":
		if ChatGoogleGenerativeAI is None:
			raise RuntimeError("Google Generative AI package not available")
		return ChatGoogleGenerativeAI(model=model_name, api_key=api_key)
	elif provider == "Anthropic":
		if ChatAnthropic is None:
			raise RuntimeError("Anthropic package not available")
		return ChatAnthropic(model=model_name, api_key=api_key)
	elif provider == "Custom/Local":
		# Use OpenAI-compatible endpoint for local/custom servers
		return ChatOpenAI(model="unknown", api_key=api_key, base_url=endpoint)  # model may be ignored by server
	else:
		raise ValueError(f"Unsupported provider: {provider}")


def create_agent_executor() -> AgentExecutor:
	"""Create an AgentExecutor configured from FreeCAD preferences."""
	prefs = _load_preferences()
	model_name = prefs.get("selected_model")
	api_keys = prefs.get("api_keys", [])
	system_prompt = prefs.get("system_prompt") or (
		"You are a helpful FreeCAD assistant. To perform any action (create, modify, delete, query), "
		"you MUST call the execute_freecad_python tool with the complete, syntactically correct Python script."
	)

	if not model_name:
		raise RuntimeError("No model selected. Please configure a model in Preferences.")

	provider = _find_provider_for_model(model_name)
	if not provider:
		raise RuntimeError(f"Unable to determine provider for model: {model_name}")

	creds = _get_provider_credentials(api_keys, provider)
	if not creds.get("api_key") and provider != "Custom/Local":
		raise RuntimeError(f"No API key found for provider: {provider}")

	model = _instantiate_model(provider, model_name, creds)

	# Tools
	tools = [ExecuteFreeCADPythonTool()]

	# Prompt
	prompt = ChatPromptTemplate.from_messages([
		("system", system_prompt),
		("human", "{input}")
	])

	agent = create_tool_calling_agent(model, tools, prompt)
	executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
	return executor
