try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui
from ui.design_tokens import RADIUS_SURFACE, SPACE_COMPOSER, SPACE_MD, WASH
from ui.typography import BODY, css


class _BubbleLabel(QtWidgets.QLabel):
    """
    A wrapped label as wide as its own words, up to a share of the column.

    `QLabel.sizeHint` stops meaning anything once the label wraps: it is an
    internal compromise made at no width in particular, and it comes back
    smaller the more room the label is given -- 119px for four words that need
    145px on one line, and 83px for the same four once a 400px maximum was
    set. Under the bubble's `Maximum` size policy that guess is a ceiling
    nothing can pass, which is how a four-word message wrapped into four lines
    in a dock wide enough for forty.

    `QFontMetrics` is not the answer either: it measures a string, and the
    label lays out a paragraph. "Зроби модель з першої фази" advances 195px
    and the padding is 32, but the label needs 234 before it will keep the
    sentence on one line -- 7px more than the metrics say, so the last word
    wrapped and a 445px panel showed a 225px bubble. The width is asked of the
    label instead: the narrowest one that costs no more lines than the cap
    does, found by bisection because `heightForWidth` only ever falls as the
    width rises. The metrics stay as the floor, because a single unbreakable
    word never costs a second line and the search would otherwise run to 1px.
    """

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setWordWrap(True)
        self._cap = 10_000

    def cap_at(self, width: int):
        self._cap = width
        self.updateGeometry()

    def sizeHint(self):
        metrics = QtGui.QFontMetrics(self.font())
        margins = self.contentsMargins()
        narrowest = min(max(metrics.horizontalAdvance(line)
                            for line in self.text().split("\n"))
                        + margins.left() + margins.right() + 2 * self.margin(),
                        self._cap)
        widest = self._cap
        fewest = self.heightForWidth(widest)
        while narrowest < widest:
            between = (narrowest + widest) // 2
            if self.heightForWidth(between) <= fewest:
                widest = between
            else:
                narrowest = between + 1
        return QtCore.QSize(narrowest, self.heightForWidth(narrowest))


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

    SHORT_OF_THE_COLUMN = 0.78

    def __init__(self, text: str = ""):
        super().__init__()
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)

        self.text_label = _BubbleLabel(text)
        self.text_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.text_label.setStyleSheet(
            "QLabel { background-color: %s; border-radius: %dpx; "
            "padding: %dpx %dpx; %s }"
            % (WASH, RADIUS_SURFACE, SPACE_COMPOSER, SPACE_MD, css(BODY)))

        self.text_label.setSizePolicy(QtWidgets.QSizePolicy.Maximum,
                                      QtWidgets.QSizePolicy.Preferred)
        layout.addWidget(self.text_label)
        self.setLayout(layout)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.text_label.cap_at(int(self.width() * self.SHORT_OF_THE_COLUMN))


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
