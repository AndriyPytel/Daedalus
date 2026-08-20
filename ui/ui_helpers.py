import os

import FreeCAD
import FreeCADGui
try:
    from PySide2 import QtCore, QtGui, QtWidgets, QtSvg
except ImportError:
    from PySide import QtCore, QtGui, QtSvg
    QtWidgets = QtGui

ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")


class UIHelpers:
    """Helper class for SVG icons and common UI styling."""

    @staticmethod
    def _icon_svg(name):
        """
        The named icon's raw markup, read from `ui/icons/<name>.svg`, or
        `None` if that file does not exist.

        FCC-171's asset directory: the addon's own thirteen Feather-style
        outlines, plus the five Lucide-style shapes
        (docs/research/fcc167-ai-elements-figma-spec.md names the set) the
        Queue phase marks, the Step status marks and the model-selector
        trigger need. `currentColor` stands in for the colour, same as
        before this moved out of a Python dict.
        """
        path = os.path.join(ICON_DIR, "%s.svg" % name)
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return None

    @staticmethod
    def get_icon(name, size=24, on=None):
        """
        Returns a QIcon from the SVG map, tinted to match the widget *on*.

        An SVG has to be handed a literal colour before it is rasterised, so
        this is the addon's one remaining palette read (ADR-0011) — and it asks
        the widget the icon will sit on, not QApplication, whose palette is the
        OS's and is light under every FreeCAD theme. A widget only carries the
        global stylesheet's colour once Qt has polished it, which is why the
        callers re-tint on show rather than trusting construction time. The
        role read is the widget's own foregroundRole rather than a fixed one: a
        button's stylesheet colour lands in ButtonText and a list's in Text.
        """
        svg_text = UIHelpers._icon_svg(name)
        if not svg_text:
            return QtGui.QIcon()

        source = QtWidgets.QApplication if on is None else on
        role = QtGui.QPalette.WindowText if on is None else on.foregroundRole()
        color = source.palette().color(role).name()

        styled_svg = svg_text.replace("currentColor", color)
        
        byte_array = QtCore.QByteArray(styled_svg.encode('utf-8'))
        renderer = QtSvg.QSvgRenderer(byte_array)
        
        pixmap = QtGui.QPixmap(size, size)
        pixmap.fill(QtCore.Qt.transparent)
        
        painter = QtGui.QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        
        return QtGui.QIcon(pixmap)

    @staticmethod
    def get_header_style():
        """A section heading: weight and spacing, no colour of its own (ADR-0011)."""
        return (
            "font-size: 13px; font-weight: 800; "
            "text-transform: uppercase; letter-spacing: 1.5px; padding: 4px 0 2px 0;"
        )

    @staticmethod
    def get_input_style():
        """Input geometry. The theme paints it."""
        return "QLineEdit, QTextEdit, QComboBox { border-radius: 6px; padding: 6px 10px; }"

    @staticmethod
    def get_monospace_font():
        """The monospace font FreeCAD's Python editor and console use."""
        try:
            p = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Editor")
            font = p.GetString("Font", "")
            if font:
                return font
        except Exception:
            pass
        return '"SF Mono", Monaco, Menlo, Consolas, "Liberation Mono", "Courier New", monospace'

    @staticmethod
    def get_ui_font():
        """The general UI font FreeCAD uses."""
        try:
            p = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/General")
            font = p.GetString("FontFamily", "")
            if font:
                return font
        except Exception:
            pass
        return "'Inter', 'Segoe UI', Roboto, sans-serif"
