# FreeCadCopilot/Mod/CopilotPreferences.py
"""
Registers two separate preference pages under the "FreeCadCopilot" group.
FreeCAD will show them as individual child entries in the left-hand tree,
exactly like "Appearance" appears under "Measure".

  FreeCadCopilot
  ├── API & Providers
  └── Prompt Management
"""
import FreeCADGui
from Mod.pages.providers_page import ProvidersPage
from Mod.pages.prompts_page   import PromptsPage

# Each call with the same group name ("FreeCadCopilot") creates a child page.
FreeCADGui.addPreferencePage(ProvidersPage, "FreeCadCopilot")
FreeCADGui.addPreferencePage(PromptsPage,   "FreeCadCopilot")
