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
"""
try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from ui.design_tokens import RADIUS_MD, SPACE_SM, SPACE_XS, WASH
from ui.typography import BODY, BODY_STRONG, css

PROVIDER_STEP = "provider"
MODEL_STEP = "model"


class _Row(QtWidgets.QFrame):
    """
    One selectable line, a label with a hover wash instead of a colour of its
    own (ADR-0011) — the same wash `HistoryItem` hovers with.
    """

    clicked = QtCore.Signal()

    def __init__(self, text):
        super().__init__()
        self.text = text
        self.setObjectName("ModelSelectorRow")
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setStyleSheet(
            "QFrame#ModelSelectorRow { border-radius: %dpx; background: transparent; }"
            "QFrame#ModelSelectorRow:hover { background-color: %s; }" % (RADIUS_MD, WASH))

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(SPACE_SM, SPACE_XS, SPACE_SM, SPACE_XS)
        label = QtWidgets.QLabel(text)
        label.setStyleSheet(css(BODY) + "background: transparent;")
        layout.addWidget(label)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ModelSelectorPopup(QtWidgets.QFrame):
    """
    A floating, opaque popup — the theme paints it, translucent would need a
    colour of its own (ADR-0011) — that closes itself the moment a model is
    chosen, or the moment the user clicks outside it (``Qt.Popup``).

    ``step`` names which list is on screen; ``visible_rows`` is the clickable
    rows on it, in order — a provider's own name is never one of them, so
    step 2's own heading does not count as a choice.
    """

    model_chosen = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setObjectName("ModelSelectorPopup")
        self._models = []
        self.step = PROVIDER_STEP

        container = QtWidgets.QFrame(self)
        container.setObjectName("PopupContainer")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(container)

        self._list_layout = QtWidgets.QVBoxLayout(container)
        self._list_layout.setContentsMargins(SPACE_XS, SPACE_XS, SPACE_XS, SPACE_XS)
        self._list_layout.setSpacing(0)

    def open_at(self, models: list, anchor: QtWidgets.QWidget):
        """Opens on the provider step, under *anchor*, sized to its content."""
        self._models = list(models)
        self._show_providers()
        self.move(anchor.mapToGlobal(QtCore.QPoint(0, anchor.height())))
        self.show()
        self.adjustSize()

    def visible_rows(self) -> list:
        return [self._list_layout.itemAt(i).widget().text
                for i in range(self._list_layout.count())
                if isinstance(self._list_layout.itemAt(i).widget(), _Row)]

    def _clear(self):
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

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
        self.adjustSize()

    def _show_models(self, provider: str):
        self.step = MODEL_STEP
        self._clear()
        heading = QtWidgets.QLabel(provider)
        heading.setStyleSheet(css(BODY_STRONG) + "background: transparent;")
        heading.setContentsMargins(SPACE_SM, SPACE_XS, SPACE_SM, SPACE_XS)
        self._list_layout.addWidget(heading)
        for model in self._models:
            if model["provider"] != provider:
                continue
            row = _Row(model["display"])
            row.clicked.connect(lambda m=model["id"]: self._choose(m))
            self._list_layout.addWidget(row)
        self.adjustSize()

    def _choose(self, model_id: str):
        self.model_chosen.emit(model_id)
        self.close()
