"""
Registers the addon's preference page under the "FreeCadCopilot" group.
FreeCAD shows it as a child entry in the left-hand tree, exactly like
"Appearance" appears under "Measure".

  FreeCadCopilot
  ├── General
  ├── API & Providers
  └── Role → Model

The phase prompts are not settings — they are `config/prompts/*.md`, edited as
files.
"""
import FreeCADGui
from ui.general_page import GeneralPage
from ui.providers_page import ProvidersPage
from ui.roles_page import RolesPage

FreeCADGui.addPreferencePage(GeneralPage, "FreeCadCopilot")
FreeCADGui.addPreferencePage(ProvidersPage, "FreeCadCopilot")
FreeCADGui.addPreferencePage(RolesPage, "FreeCadCopilot")
