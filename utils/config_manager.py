# FreeCadCopilot/utils/config_manager.py
import FreeCAD
import json

# NOTE: MODEL_MAP has moved to config/models.json
# Access it via utils.provider_manager.ProviderManager.instance().get_models_for_provider()

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
        
        # Specific agent overrides (if any)
        orchestrator_prompt = param_grp.GetString("OrchestratorPrompt", "")
        designer_prompt = param_grp.GetString("DesignerPrompt", "")
        developer_prompt = param_grp.GetString("DeveloperPrompt", "")
        validator_prompt = param_grp.GetString("ValidatorPrompt", "")
        reviewer_prompt = param_grp.GetString("ReviewerPrompt", "")
        
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
        """
        Backward-compat shim — delegates to SettingsManager.
        Returns (provider, model_id, api_key).
        """
        from utils.settings_manager import SettingsManager
        return SettingsManager.instance().get_llm_credentials()

    @staticmethod
    def get_monospace_font():
        """Retrieve the monospace font used in FreeCAD's Python editor/console."""
        try:
            p = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Editor")
            # "Font" is the usual key for the editor font family in FreeCAD
            font = p.GetString("Font", "")
            if font:
                return font
        except Exception:
            pass
        # Fallback stack
        return '"SF Mono", Monaco, Menlo, Consolas, "Liberation Mono", "Courier New", monospace'

    @staticmethod
    def get_ui_font():
        """Retrieve the general UI font used in FreeCAD."""
        try:
            p = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/General")
            font = p.GetString("FontFamily", "")
            if font:
                return font
        except Exception:
            pass
        # Fallback stack
        return "'Inter', 'Segoe UI', Roboto, sans-serif"
