# FreeCadCopilot/Mod/CopilotPreferences.py
"""
Registers the addon's preference page under the "FreeCadCopilot" group.
FreeCAD shows it as a child entry in the left-hand tree, exactly like
"Appearance" appears under "Measure".

  FreeCadCopilot
  └── API & Providers

The phase prompts are not settings — they are `config/prompts/*.md`, edited as
files.
"""
import FreeCADGui
from Mod.pages.providers_page import ProvidersPage

FreeCADGui.addPreferencePage(ProvidersPage, "FreeCadCopilot")
