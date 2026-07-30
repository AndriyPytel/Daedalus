import FreeCAD
import FreeCADGui
try:
    from PySide2 import QtCore, QtGui, QtWidgets, QtSvg
except ImportError:
    from PySide import QtCore, QtGui, QtSvg
    QtWidgets = QtGui

class UIHelpers:
    """Helper class for SVG icons and common UI styling."""

    # Embedded SVGs as raw strings for convenience and themed coloring
    ICONS = {
        "eye_open": """
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                <circle cx="12" cy="12" r="3"></circle>
            </svg>
        """,
        "eye_closed": """
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path>
                <line x1="1" y1="1" x2="23" y2="23"></line>
            </svg>
        """,
        "factory": """
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
            </svg>
        """,
        "user": """
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
                <circle cx="12" cy="7" r="4"></circle>
            </svg>
        """,
        "trash": """
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="3 6 5 6 21 6"></polyline>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                <line x1="10" y1="11" x2="10" y2="17"></line>
                <line x1="14" y1="11" x2="14" y2="17"></line>
            </svg>
        """,
        "refresh": """
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="23 4 23 10 17 10"></polyline>
                <polyline points="1 20 1 14 7 14"></polyline>
                <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
            </svg>
        """,
        "plus": """
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="12" y1="5" x2="12" y2="19"></line>
                <line x1="5" y1="12" x2="19" y2="12"></line>
            </svg>
        """,
        "image": """
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                <circle cx="8.5" cy="8.5" r="1.5"></circle>
                <polyline points="21 15 16 10 5 21"></polyline>
            </svg>
        """,
        "file_text": """
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                <polyline points="14 2 14 8 20 8"></polyline>
                <line x1="16" y1="13" x2="8" y2="13"></line>
                <line x1="16" y1="17" x2="8" y2="17"></line>
                <polyline points="10 9 9 9 8 9"></polyline>
            </svg>
        """,
        "attachment": """
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2.001 2.001 0 1 1-2.83-2.83l8.49-8.48"></path>
            </svg>
        """,
        "close": """
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
        """,
        "arrow_up": """
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
                <line x1="12" y1="19" x2="12" y2="5"></line>
                <polyline points="5 12 12 5 19 12"></polyline>
            </svg>
        """,
        "history": """
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M3 3v5h5"></path>
                <path d="M3.05 13A9 9 0 1 0 6 5.3L3 8"></path>
                <polyline points="12 7 12 12 15 14"></polyline>
            </svg>
        """
    }

    @staticmethod
    def get_icon(name, size=24, on=None):
        """
        Returns a QIcon from the SVG map, tinted to match the widget *on*.

        An SVG has to be handed a literal colour before it is rasterised, so
        this is the addon's one remaining palette read (ADR-0011) — and it asks
        the widget the icon will sit on, not QApplication, whose palette is the
        OS's and is light under every FreeCAD theme. A widget only carries the
        global stylesheet's colour once Qt has polished it, which is why the
        callers re-tint on show rather than trusting construction time.
        """
        svg_text = UIHelpers.ICONS.get(name)
        if not svg_text:
            return QtGui.QIcon()

        # foregroundRole, not a fixed role: a button's stylesheet colour lands
        # in ButtonText and a list's in Text.
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
