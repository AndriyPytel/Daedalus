# Dock Widget Fix

## Issue
```
module 'FreeCADGui' has no attribute 'addDockWidget'
```

## Solution
FreeCAD requires dock widgets to be added via the main window, not directly through `FreeCADGui`.

### Changes Made to `Mod/CopilotWorkbench.py`:

**Before:**
```python
def Activated(self):
    if self.copilot_panel is None or not self.copilot_panel.isVisible():
        self.copilot_panel = CopilotPanel()
        Gui.addDockWidget(0x0008, self.copilot_panel)  # WRONG!

def Deactivated(self):
    if self.copilot_panel:
        Gui.removeDockWidget(self.copilot_panel)  # WRONG!
```

**After:**
```python
def Activated(self):
    from PySide import QtCore
    import FreeCAD
    
    mw = Gui.getMainWindow()  # Get the main window
    if mw is None:
        FreeCAD.Console.PrintError("Cannot get main window\n")
        return
    
    if self.copilot_panel is None:
        self.copilot_panel = CopilotPanel()
    
    # Add dock widget via main window
    mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.copilot_panel)
    self.copilot_panel.show()

def Deactivated(self):
    if self.copilot_panel:
        self.copilot_panel.hide()  # Simply hide the panel
```

### Key Points:
1. Get the main window: `mw = Gui.getMainWindow()`
2. Add dock widget via main window: `mw.addDockWidget(...)`
3. Use `QtCore.Qt.RightDockWidgetArea` instead of hex constants
4. Show/hide the panel instead of add/remove

### Also Fixed:
- `InitGui.py`: Removed parentheses from `Gui.addWorkbench()` call
