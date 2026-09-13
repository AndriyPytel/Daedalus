"""
The Model Selector: a popup that picks a model in two steps, provider then
that provider's models replacing it in place (FCC-175, FCC-169).

Not the design-exact ``ModelSelectorContent`` — one dialog with search and
every provider's models grouped inside it — but the sequential flow the epic
settled on instead: the popup's own content is swapped, provider list for
model list, rather than nesting one inside the other. It always opens back on
the provider step; nothing here remembers which provider was picked last, so
a reopen after choosing a model shows providers again, not that model's list.

Its rows read the same ``[{id, display, provider}]`` shape
``ProviderManager.get_available_models`` hands the picker it replaces, so a
provider with no configured key never reaches it either.

FCC-169's user verdict on the first cut of this: no frame around it, and it
must open *upward*. The trigger sits at the bottom of a narrow docked panel
(the composer's tool strip), so a popup that always drops down runs off the
bottom of the screen — see ``_reflow``.
"""
try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from ui.design_tokens import (CONTROL, ICON, RADIUS_CONTROL,
                              RADIUS_SURFACE, SPACE_XS, SPACE_SM, WASH)
from ui.typography import BODY, BODY_STRONG, css
from ui.ui_helpers import UIHelpers

PROVIDER_STEP = "provider"
MODEL_STEP = "model"


def _screen_for(widget) -> QtGui.QScreen:
    """The screen *widget* is on, however this Qt binding says so.
    ``QWidget.screen()`` exists only from Qt 5.14 -- FreeCAD's PySide
    shim has it (re-exports PySide6) but a bare PySide2 might not, so
    this falls back to the application's screen-at-position lookup, then
    to the primary screen when that returns ``None`` (widget not yet shown).
    """
    getter = getattr(widget, "screen", None)
    if callable(getter):
        found = getter()
        if found is not None:
            return found
    found = QtGui.QGuiApplication.screenAt(widget.mapToGlobal(QtCore.QPoint(0, 0)))
    return found or QtWidgets.QApplication.primaryScreen()


def _laid_out(layout) -> QtCore.QSize:
    """How much room *layout*'s widgets need, read off the widgets themselves.
    A widget pinned with `setFixedHeight` keeps its prior size hint -- the
    pin is a min/max, the hint neither -- so the larger wins. A row works
    out its own layout first, since one built and measured in the same
    event-loop turn answers with a default fitting none of its text.
    """
    margins = layout.contentsMargins()
    width = height = 0
    for position in range(layout.count()):
        widget = layout.itemAt(position).widget()
        if widget is None:
            continue
        if widget.layout() is not None:
            widget.layout().activate()
        hint = widget.sizeHint()
        height += max(hint.height(), widget.minimumHeight())
        height += layout.spacing() if position else 0
        width = max(width, hint.width(), widget.minimumWidth())
    return QtCore.QSize(width + margins.left() + margins.right(),
                        height + margins.top() + margins.bottom())


class _Row(QtWidgets.QFrame):
    """One selectable line: a label, a hover wash instead of a colour of
    its own (ADR-0011, the same wash `HistoryItem` uses), and for the
    active model row a trailing check, not a recolour (state is never a
    repaint). Nothing leads the label -- this addon ships no brand
    marks, and one glyph for all of them would say only a mark exists.
    """

    clicked = QtCore.Signal()

    def __init__(self, text, checked=False):
        super().__init__()
        self.text = text
        self._checked = checked
        self.setObjectName("ModelSelectorRow")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedHeight(CONTROL)
        self.setStyleSheet(
            "QFrame#ModelSelectorRow { border-radius: %dpx; background: transparent; }"
            "QFrame#ModelSelectorRow:hover { background-color: %s; }" % (RADIUS_CONTROL, WASH))

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(SPACE_SM, 0, SPACE_SM, 0)
        layout.setSpacing(SPACE_XS)

        label = QtWidgets.QLabel(text)
        label.setStyleSheet(css(BODY) + "background: transparent;")
        layout.addWidget(label, 1)

        self._check_icon = QtWidgets.QLabel()
        self._check_icon.setFixedSize(ICON, ICON)
        layout.addWidget(self._check_icon)
        self._check_icon.setVisible(checked)

    def showEvent(self, event):
        """
        The check is tinted once the row is on screen, like every other icon in
        the addon — asked any earlier it still answers with the OS palette,
        not the one FreeCAD's global stylesheet has since painted it with
        (ADR-0011).
        """
        super().showEvent(event)
        if self._checked:
            self._check_icon.setPixmap(
                UIHelpers.get_icon("circle_check", size=ICON, on=self).pixmap(ICON, ICON))

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _ListPopup(QtWidgets.QFrame):
    """
    A floating, opaque list that closes itself the moment something is picked
    or the user clicks outside it (``Qt.Popup``).

    ``PopupContainer`` reads as a floating surface the way the visual
    grammar's separation-strategy level 4 does elsewhere in this addon
    (`InputContainer`, `PlanCard`), minus the hairline: a top-level
    ``Qt.Popup`` has nothing behind it, so a hairline is the one edge this
    surface can still afford to skip and the fill is what has to carry the
    whole of it — the user asked twice for no border around this picker. The
    fill itself is resolved when the popup opens, not here: it has to be
    `UIHelpers.surface_colour(anchor)`, the anchor's own theme, not a wash
    that composites onto whatever a bare top-level window is handed, which is
    white regardless of FreeCAD's own dark theme. Radius stays
    `RADIUS_SURFACE`: the grammar's own `ModelSelectorContent` uses a 14px
    radius this design system has no token for, and every other floating card
    in this addon already uses `RADIUS_SURFACE` (10), which reads as "this
    addon's popovers" more than one borrowed number would.
    `Qt.Popup | Qt.FramelessWindowHint` drops the OS window frame so the fill
    and its radius are the only edge this popup has.

    ``visible_rows`` is the clickable rows on it, in order — a heading is
    never one of them, so a list's own title does not count as a choice. The
    rows sit in a scroll area capped by `_reflow` to whatever room was
    measured above or below the trigger, so a long list scrolls in place
    rather than growing the popup past the screen.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self._anchor = None

        self._container = QtWidgets.QFrame(self)
        self._container.setObjectName("PopupContainer")

        self._scroll = QtWidgets.QScrollArea(self._container)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("background: transparent;")

        list_container = QtWidgets.QWidget()
        list_container.setStyleSheet("background: transparent;")
        self._list_layout = QtWidgets.QVBoxLayout(list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(0)
        self._scroll.setWidget(list_container)

    def _dress(self, anchor: QtWidgets.QWidget):
        """
        Take the anchor's theme and stand against it.

        The fill is resolved per open rather than once at construction: the
        anchor is not known until a caller opens the popup against one, and
        the theme it carries can change between one open and the next.
        """
        self._anchor = anchor
        self._container.setStyleSheet(
            "QFrame#PopupContainer { background-color: %s; border-radius: %dpx; }"
            % (UIHelpers.surface_colour(anchor), RADIUS_SURFACE))

    def _reflow(self):
        """
        Sizes and places the popup against `self._anchor`, never off-screen.

        Measures the room above and below the trigger on its own screen
        first, picks whichever side has more of it — above by default, below
        only when that measurement says below actually has more room, per
        FCC-169 — then caps the scroll area to that room so a long list
        scrolls inside the popup instead of growing it past the screen edge.

        `self._container` and `self._scroll` are positioned with an explicit
        `setGeometry` rather than a `QVBoxLayout` wrapping them: a popup this
        method repositions on every step change needs a `QLayout` on a
        *top-level* widget to never react on its own, and it does not — a
        layout invalidated by a child (here, `self._scroll` growing from the
        provider step's two rows to a longer model list) schedules a
        deferred re-layout of its own that lands after this method returns
        and silently overwrites the size just computed, back to the size the
        popup had before this call. `inset` is `SPACE_XS`, the fill's own
        padding and nothing else now the hairline border is gone, measured by
        hand for the same reason: reading it back off a layout's sizeHint is
        exactly the read this method exists to avoid.

        The list is measured off its own rows rather than by asking the
        container how big it wants to be. A row added or removed posts a
        layout request answered a turn of the event loop later, and until it
        is answered the container answers with the size it had before the step
        change -- or with zero, for one built into a popup never yet shown.
        """
        if self._anchor is None:
            return

        anchor = self._anchor
        anchor_rect = QtCore.QRect(anchor.mapToGlobal(QtCore.QPoint(0, 0)), anchor.size())
        screen_rect = _screen_for(anchor).availableGeometry()

        space_above = anchor_rect.top() - screen_rect.top()
        space_below = screen_rect.bottom() - anchor_rect.bottom()
        open_above = space_above >= space_below
        available = max(space_above, space_below)

        gap = SPACE_XS
        inset = SPACE_XS
        content_size = _laid_out(self._scroll.widget().layout())

        scroll_height = min(content_size.height(), max(available - gap - 2 * inset, CONTROL))
        width = max(content_size.width() + 2 * inset, anchor.width())
        width = min(width, screen_rect.width())
        height = scroll_height + 2 * inset

        self._container.setGeometry(0, 0, width, height)
        self._scroll.setGeometry(inset, inset, width - 2 * inset, scroll_height)

        x = anchor_rect.left()
        x = max(screen_rect.left(), min(x, screen_rect.right() - width + 1))

        if open_above:
            y = anchor_rect.top() - gap - height
        else:
            y = anchor_rect.bottom() + gap
        y = max(screen_rect.top(), min(y, screen_rect.bottom() - height + 1))

        self.setFixedSize(width, height)
        self.move(x, y)

    def _current_label(self) -> str:
        """What the trigger says it is set to, which is what carries the check."""
        get_text = getattr(self._anchor, "text", None)
        return get_text() if callable(get_text) else ""

    def visible_rows(self) -> list:
        return [self._list_layout.itemAt(i).widget().text
                for i in range(self._list_layout.count())
                if isinstance(self._list_layout.itemAt(i).widget(), _Row)]

    def _heading(self, text: str):
        heading = QtWidgets.QLabel(text)
        heading.setStyleSheet(css(BODY_STRONG) + "background: transparent;")
        heading.setContentsMargins(SPACE_SM, SPACE_XS, SPACE_SM, SPACE_XS)
        self._list_layout.addWidget(heading)

    def _clear(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.deleteLater()


class ModelSelectorPopup(_ListPopup):
    """
    The model picker: providers first, then that provider's models in place.

    ``step`` names which list is on screen.
    """

    model_chosen = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ModelSelectorPopup")
        self._models = []
        self._current_display = ""
        self.step = PROVIDER_STEP

    def open_at(self, models: list, anchor: QtWidgets.QWidget):
        """Opens on the provider step, against *anchor* -- above it when there
        is more room above than below, below otherwise (`_reflow`), sized to
        its content up to whatever room that measurement found.
        The current model is read off *anchor*'s own label
        (`_set_current_model` put it there), not a new parameter: the public
        `open_at(models, button)` API `copilot_panel.py` keeps already
        carries it in the button's text. The fill resolves here too, against
        the same *anchor*, since its theme can change between opens.
        """
        self._models = list(models)
        self._dress(anchor)
        self._current_display = self._current_label()
        self._show_providers()
        self.show()

    def _show_providers(self):
        self.step = PROVIDER_STEP
        self._clear()
        seen = []
        for model in self._models:
            if model["provider"] not in seen:
                seen.append(model["provider"])
        for provider in seen:
            row = _Row(provider)
            row.clicked.connect(lambda p=provider: self._show_models(p))
            self._list_layout.addWidget(row)
        self._reflow()

    def _show_models(self, provider: str):
        self.step = MODEL_STEP
        self._clear()
        self._heading(provider)
        for model in self._models:
            if model["provider"] != provider:
                continue
            row = _Row(model["display"], checked=model["display"] == self._current_display)
            row.clicked.connect(lambda m=model["id"]: self._choose(m))
            self._list_layout.addWidget(row)
        self._reflow()

    def _choose(self, model_id: str):
        self.model_chosen.emit(model_id)
        self.close()


class EffortPopup(_ListPopup):
    """How hard the model is asked to think, in one flat list.

    A second picker, not a dialog setting: the same kind of choice as
    the model, made at the same moment, changed to compare one run
    against the next. Levels are the routes' own: `--effort` for the
    Claude Code CLI, `reasoning_effort` for LiteLLM (`core.llm`). A
    model that cannot reason is never offered the choice -- a picker
    that changes nothing is a picker that lies.
    """

    effort_chosen = QtCore.Signal(str)

    def open_at(self, levels: list, anchor: QtWidgets.QWidget):
        """*levels* in the order they are offered, the current one checked."""
        self._dress(anchor)
        current = self._current_label()
        self._clear()
        for level in levels:
            row = _Row(level, checked=level == current)
            row.clicked.connect(lambda chosen=level: self._choose(chosen))
            self._list_layout.addWidget(row)
        self._reflow()
        self.show()

    def _choose(self, level: str):
        self.effort_chosen.emit(level)
        self.close()
