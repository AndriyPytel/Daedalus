# FreeCadCopilot/utils/config_manager.py
import FreeCAD
import json

class ConfigManager:
    @staticmethod
    def get_preferences():
        param_grp = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/FreeCadCopilot")
        
        # Load API Keys
        api_keys_json = param_grp.GetString("ApiKeys", "[]")
        try:
            api_keys = json.loads(api_keys_json)
        except json.JSONDecodeError:
            api_keys = []
            
        selected_model = param_grp.GetString("SelectedModel", "")
        global_system_prompt = param_grp.GetString("GlobalSystemPrompt", "You are a helpful AI Assistant for FreeCAD.")
        
        # For now, default all agents to the global prompt if they don't have a specific one
        # In the future, we will have specific prompts
        orchestrator_prompt = param_grp.GetString("OrchestratorPrompt", global_system_prompt)
        designer_prompt = param_grp.GetString("DesignerPrompt", global_system_prompt)
        developer_prompt = param_grp.GetString("DeveloperPrompt", global_system_prompt)
        validator_prompt = param_grp.GetString("ValidatorPrompt", global_system_prompt)
        reviewer_prompt = param_grp.GetString("ReviewerPrompt", global_system_prompt)
        
        return {
            "api_keys": api_keys,
            "selected_model": selected_model,
            "global_system_prompt": global_system_prompt,
            "orchestrator_prompt": orchestrator_prompt,
            "designer_prompt": designer_prompt,
            "developer_prompt": developer_prompt,
            "validator_prompt": validator_prompt,
            "reviewer_prompt": reviewer_prompt
        }

    @staticmethod
    def get_llm_credentials():
        prefs = ConfigManager.get_preferences()
        selected_model = prefs["selected_model"]
        api_keys = prefs["api_keys"]
        
        if not selected_model or not api_keys:
            raise ValueError("Model or API keys not configured in FreeCAD preferences.")

        from ai_handlers.AgentFactory import MODEL_MAP
        
        provider = ""
        api_key = ""
        for key_info in api_keys:
            provider_name = key_info.get("provider", "")
            if provider_name in MODEL_MAP and selected_model in MODEL_MAP[provider_name]:
                provider = provider_name
                api_key = key_info.get("api_key")
                break
                
        if not provider or not api_key:
            raise ValueError(f"Could not find a valid API key for the selected model: {selected_model}")
            
        return provider, selected_model, api_key
