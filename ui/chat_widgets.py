import html
import os

try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui
from ui.design_tokens import RADIUS_SURFACE, SPACE_COMPOSER, SPACE_MD, WASH
from ui.typography import BODY, css
from ui.ui_helpers import UIHelpers


NAME_KEPT = 28


def _cut_to_fit(name: str) -> str:
    """*name* with its middle elided, where it is longer than `NAME_KEPT`."""
    if len(name) <= NAME_KEPT:
        return name
    head = (NAME_KEPT - 1) // 2
    return name[:head] + "…" + name[len(name) - (NAME_KEPT - 1 - head):]


class TextMessageWidget(QtWidgets.QWidget):
    """
    A chat bubble for user text, hugging the right edge.

    The bubble is a translucent grey rather than a colour: it darkens a light
    theme and lightens a dark one. It is pushed right and left short of the
    full width because that is what tells a reader, without a label, which
    turns in the transcript are theirs -- everything the run says is flush
    left and unboxed. It is the one message shape in the design system given a
    surface of its own, and it is padded the way a surface is.

    The margins are the scrolling column's; this widget adds none.
    """

    def __init__(self, text: str = ""):
        super().__init__()
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)

        self.text_label = QtWidgets.QLabel(text)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.text_label.setStyleSheet(
            "QLabel { background-color: %s; border-radius: %dpx; "
            "padding: %dpx %dpx; %s }"
            % (WASH, RADIUS_SURFACE, SPACE_COMPOSER, SPACE_MD, css(BODY)))

        self.text_label.setSizePolicy(QtWidgets.QSizePolicy.Maximum,
                                      QtWidgets.QSizePolicy.Preferred)
        layout.addWidget(self.text_label)
        self.setLayout(layout)


class AssistantMessageWidget(QtWidgets.QWidget):
    """
    Plain text layout for assistant responses (no bubble).

    Flush left and unboxed against the user's bubble on the right: no fill, no
    border and no padding of its own, which is the cheapest separation the
    design system has and the one it gives everything the run says.

    The margins are the scrolling column's; this widget adds none.
    """

    def __init__(self, text: str = ""):
        super().__init__()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.text_label = QtWidgets.QLabel(text)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.text_label.setStyleSheet("QLabel { background-color: transparent; %s }"
                                      % css(BODY))

        layout.addWidget(self.text_label)
        self.setLayout(layout)

    def append_text(self, text: str):
        self.text_label.setText(self.text_label.text() + text)


class DocumentLinkWidget(AssistantMessageWidget):
    """
    An assistant line that opens a file when it is clicked.

    The description Design ends in is a document on disk, and the Plan Gate
    stands over it by linking to it: what a user opens to correct a misreading
    is the very file the run is planned from, not a rendering of it in the
    chat. The text is escaped because the line carries a path, and a path is
    whatever the drawing was called.

    The link is written in the same ink as the sentence it ends, not in
    Qt's own `QPalette.Link` blue: the addon wears the user's theme and owns
    no colour of its own (ADR-0011), and a bright underlined path is the
    loudest thing in a transcript that has a Plan in it.

    The path is taken out of the sentence and the file name is what is
    clicked. A `Library/Application Support/...` path set as link text wraps
    across three lines of underlined blue and is the loudest thing in the
    transcript, which is the wrong emphasis for a file a user opens once. The
    name is cut in the middle for the same reason, since a name this addon
    writes ends in a run id and a timestamp: `NAME_KEPT` characters is what
    fits one line beside its sentence at the panel's usual width. The whole
    path stays reachable as the tooltip.
    """

    def __init__(self, text: str, path: str):
        super().__init__()
        name = _cut_to_fit(os.path.basename(path) or path)
        prose = text.replace(path, "").strip().rstrip(":").strip()
        self.text_label.setText(
            '%s <a href="%s" style="color: %s; text-decoration: none;">%s</a>'
            % (html.escape(prose), QtCore.QUrl.fromLocalFile(path).toString(),
               UIHelpers.ink_colour(self.text_label), html.escape(name)))
        self.text_label.setToolTip(path)
        self.text_label.setTextInteractionFlags(QtCore.Qt.TextBrowserInteraction)
        self.text_label.setOpenExternalLinks(True)
