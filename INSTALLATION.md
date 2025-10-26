# Installation Guide

## Installation Steps

1. **Copy to FreeCAD Mods directory**
   ```bash
   # On Linux
   cp -r /home/undrick/Documents/Projects/FreeCadCopilot ~/.FreeCAD/Mod/FreeCadCopilot
   
   # On macOS
   cp -r /path/to/FreeCadCopilot ~/Library/Preferences/FreeCAD/Mod/FreeCadCopilot
   
   # On Windows
   # Copy to: C:\Users\YourUsername\AppData\Roaming\FreeCAD\Mod\FreeCadCopilot
   ```

2. **Restart FreeCAD**

3. **Select the Workbench**
   - Open FreeCAD
   - Select "FreeCad Copilot" from the workbench dropdown menu
   - The Copilot panel should appear on the right side

## Testing the UI (Without FreeCAD)

To test the UI without actually running FreeCAD, you can use this test script:

```python
# test_ui.py
import sys

# Mock FreeCAD and FreeCADGui
class MockFreeCAD:
    ActiveDocument = None
    @staticmethod
    def newDocument(name):
        return None

class MockFreeCADGui:
    @staticmethod
    def addDockWidget(area, widget):
        pass
    @staticmethod
    def removeDockWidget(widget):
        pass
    @staticmethod
    def updateGui():
        pass

sys.modules['FreeCAD'] = MockFreeCAD()
sys.modules['FreeCADGui'] = MockFreeCADGui()

# Now import the UI components
from PySide2.QtWidgets import QApplication
from ui.copilot_panel import CopilotPanel
from ui.settings_dialog import SettingsDialog
from ui.chat_widgets import TextMessage, CodeBlockWidget

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Test the panel
    panel = CopilotPanel()
    panel.show()
    
    # Test settings dialog
    dialog = SettingsDialog()
    # dialog.exec_()
    
    sys.exit(app.exec_())
```

## Current Status

### ✅ Completed
- [x] File structure scaffolding
- [x] Init.py and InitGui.py
- [x] CopilotWorkbench implementation
- [x] UI Panel (copilot_panel.py)
- [x] Settings Dialog
- [x] Custom chat widgets (TextMessage, CodeBlockWidget)
- [x] Markdown parser
- [x] Command executor (transactional)
- [x] Undo manager
- [x] Base AI handler
- [x] Gemini handler (placeholder)

### 🔄 Next Steps
- [ ] Connect CommandExecutor to UI
- [ ] Connect UndoManager to UI
- [ ] Implement actual Gemini API calls (async)
- [ ] Add settings persistence (FreeCAD.ParamGet)
- [ ] Implement async AI communication with QThread
- [ ] Add error handling and user feedback
- [ ] Test in actual FreeCAD environment

## Troubleshooting

### Import Errors
If you see import errors for PySide2, make sure FreeCAD is providing it. FreeCAD includes PySide2 or PyQt5.

### Panel Not Showing
- Check that the workbench is activated
- Try View → Panels → FreeCad Copilot (if available)
- Check FreeCAD's Report View for errors

### Settings Not Saving
This is expected - settings persistence is not yet implemented (Next Steps).

## Development Notes

- The UI currently uses mock responses to demonstrate the chat interface
- Code blocks can be added and their Execute/Revert buttons work (UI only)
- Actual code execution is stubbed in `_on_execute_code`
- The undo manager is implemented but not yet wired up to the panel
