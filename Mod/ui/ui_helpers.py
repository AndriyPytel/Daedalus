import functools
import os

import FreeCAD
import FreeCADGui
try:
    from PySide2 import QtCore, QtGui, QtWidgets, QtSvg
except ImportError:
    from PySide import QtCore, QtGui, QtSvg
    QtWidgets = QtGui

ICON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "resources", "icons"
)


class UIHelpers:
    """Helper class for SVG icons and common UI styling."""

    @staticmethod
    @functools.lru_cache(maxsize=None)
    def _icon_svg(name):
        """
        The named icon's raw markup, read from `Resources/icons/<name>.svg`,
        or `None` if that file does not exist.

        The set is Untitled UI Icons (untitleduico/icons, MIT), one file per
        name, 24x24 with a 2px stroke and round caps -- the whole directory is
        one hand rather than the Feather outlines and Lucide shapes it was
        assembled from, which agreed on a grid but not on a weight or a
        corner. `brain` is the one exception and is Lucide's, because the free
        Untitled UI distribution has no brain and Figma's own Reasoning header
        (component `2008:15945`, `<BrainIcon>`) is a flattened Lucide `brain`.

        `currentColor` stands in for the colour: `get_icon` replaces it with a
        literal before the SVG is rasterised, so every file must carry it.

        The markup is cached because `get_icon` is called on every repaint --
        `QueueWidget._repaint_phases` alone asks six times per event -- and an
        icon file does not change while FreeCAD is running.
        """
        path = os.path.join(ICON_DIR, "%s.svg" % name)
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return None

    @staticmethod
    def get_icon(name, size=24, on=None, colour=None):
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

        *colour* overrides that for the one thing colour is still allowed to
        say: which state something is in. The design language this panel
        follows never repaints a row or a button to report a state — the mark
        beside it changes instead — so the semantic colours ADR-0011 keeps land
        on a glyph and nowhere else. A `palette(role)` is resolved here rather
        than by the caller, because a rasteriser needs a literal and this is
        the one place in the addon allowed to ask the palette for one.
        """
        svg_text = UIHelpers._icon_svg(name)
        if not svg_text:
            return QtGui.QIcon()

        source = QtWidgets.QApplication if on is None else on
        role = QtGui.QPalette.WindowText if on is None else on.foregroundRole()
        if colour and colour.startswith("palette("):
            named = colour[len("palette("):-1]
            role = getattr(QtGui.QPalette,
                           "".join(part.capitalize() for part in named.split("-")))
            colour = source.palette().color(role).name()
        color = colour or source.palette().color(role).name()

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
    def surface_colour(on):
        """
        An opaque hex colour for a popup's own fill, resolved from *on* -- the
        widget the popup opens against, never `QApplication`.

        `Qt.Popup | Qt.FramelessWindowHint` makes a top-level window with
        nothing behind it, so a translucent wash (`WASH`) composites onto
        whatever the platform hands a bare top-level window instead of the
        docked panel it floats over -- white, under the theme this addon is
        meant to wear (ADR-0011). The fix is to stop being translucent: read
        an opaque colour off the theme instead, at the one site allowed to
        ask a palette for one (the same exception `get_icon` already uses).

        `on.window()` rather than *on* itself: a ghost control (a button
        with `background: transparent`) answers its own `Window` role with
        whatever Qt's style engine leaves there once that rule is applied,
        which is not the panel's fill -- the top-level ancestor is where
        FreeCAD's global stylesheet actually wrote a `background-color` rule
        for Qt's polish step to carry into a `QPalette`, the same mechanism
        `get_icon`'s `color:` read already relies on. Forced fully opaque on
        the way out, because a theme's own alpha is not this popup's to
        inherit -- it is what makes itself opaque, not the colour it reads.
        """
        colour = QtGui.QColor(on.window().palette().color(QtGui.QPalette.Window))
        colour.setAlpha(255)
        return colour.name(QtGui.QColor.HexRgb)

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
