# INTEGRATIONS

## 1. FreeCAD Application API
The add-on tightly integrates into FreeCAD's core and GUI loops:
- **`FreeCAD` (`App`)**: Used for document mutation, transaction management (`openTransaction`, `commitTransaction`, `abortTransaction`), and core features.
- **`FreeCADGui` (`Gui`)**: Used for adding workbenches, preferences, displaying UI panels, and main-thread task delegation (`addTodo`).

## 2. LLM Providers
- Handled via `utils/provider_manager.py`.
- Agents rely on LangChain's internal adapters or custom HTTP clients to interact with external model APIs (OpenAI, etc.). Key resolutions are fetched dynamically from FreeCAD's persistent preferences.

## 3. Storage & Configuration
- **FreeCAD Parameters (`ParamGet`)**: The `settings_manager` and `config_manager` read/write plugin settings directly into FreeCAD's native XML configuration registry (under `User parameter:BaseApp/Preferences/Mod/FreeCadCopilot`).
