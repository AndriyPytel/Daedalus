"""
The Daedalus chat is a fixture of the main window, installed at startup like
Tasks rather than built on the workbench's `Activated` and hidden on
`Deactivated`, since a conversation about the part being modelled is not a
reason to leave the workbench the part is modelled in. Every workbench has
it, so which one is active stops being a question anybody asks. One panel
per FreeCAD holds the Sessions, the worker thread and the Preflight
process, and a second of those is a second of all three.
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
        from ui.daedalus_panel import DaedalusPanel

        _panel = DaedalusPanel()
        main_window.addDockWidget(QtCore.Qt.RightDockWidgetArea, _panel)
    return _panel


def panel():
    """The installed panel, or None. Nothing here builds one."""
    return _panel


def forget():
    """Drop the reference, for a test that wants a window of its own."""
    global _panel
    _panel = None
