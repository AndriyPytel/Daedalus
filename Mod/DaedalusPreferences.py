"""
Registers the addon's preference page under the "Daedalus" group.
FreeCAD shows it as a child entry in the left-hand tree, exactly like
"Appearance" appears under "Measure".

  Daedalus
  └── API & Providers

The prompts are not settings — they are `config/prompts/*.md`, edited as files.
"""
import FreeCADGui
from ui.providers_page import ProvidersPage

FreeCADGui.addPreferencePage(ProvidersPage, "Daedalus")
