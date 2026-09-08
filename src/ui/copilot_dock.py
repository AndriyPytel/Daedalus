"""
The Copilot chat as a fixture of the main window.

It used to be the workbench's: built on `Activated`, hidden on `Deactivated`,
so the chat was only there while the user was standing in the Copilot
workbench. A conversation about the part being modelled is not a reason to
leave the workbench the part is modelled in — so the dock is installed on the
main window at startup, like Tasks, and every workbench has it. Which one is
active stops being a question anybody asks.

One panel per FreeCAD: it holds the Sessions, the worker thread and the
Preflight process, and a second of those is a second of all three.
"""
try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

_panel = None


def install(main_window):
    """
    Dock the panel on *main_window*, building it the first time.

    Returns the panel, or None when there is no window to dock it on — the
    caller is FreeCAD's GUI start-up, which is not a place to raise from.
    Qt's own View ▸ Panels lists it from here, so showing and hiding it needs
    no command of ours.
    """
    global _panel
    if main_window is None:
        return None
    if _panel is None:
        from ui.copilot_panel import CopilotPanel

        _panel = CopilotPanel()
        main_window.addDockWidget(QtCore.Qt.RightDockWidgetArea, _panel)
    return _panel


def panel():
    """The installed panel, or None. Nothing here builds one."""
    return _panel


def forget():
    """Drop the reference, for a test that wants a window of its own."""
    global _panel
    _panel = None
