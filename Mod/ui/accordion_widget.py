# type: ignore  # PySide2 imported via FreeCAD shim – static analyser can't resolve it
"""
One collapsible row in the transcript.

Thinking, tool calls and errors all take this shape: a toggle line and, under
it, wrapped text laid out in the column itself. What it used to be — a
QTextEdit inside the chat's own scroll area, animated to a magic 1000 — is what
truncated long thinking and what stole the mouse wheel from the chat (ADR-0011).

Code in a row is coloured by one pass of one regex over the string rather than
by a `QSyntaxHighlighter`: a highlighter needs a `QTextDocument` and this row
is a `QLabel`. `pygments` would do it better and is not something an addon may
assume is importable inside FreeCAD. The colours are read from FreeCAD's own
editor parameters, never chosen here.

Boxed or not, by what the row is. Reasoning and a folded list carry nothing of
their own and rely on the column for their edges, which is the cheapest way
this design language separates anything. A call the run made is the one shape
Figma's Tool card gives a border to: a hairline and no fill, so that a
transcript of a dozen calls stays a column of outlines rather than a stack of
filled slabs.
"""
import json
import keyword
import re
import time

try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui
from ui.design_tokens import ICON, ICON_DENSE, SPACE_COMPOSER, SPACE_MD, SPACE_SM, SPACE_XS
from ui.typography import CAPTION, LABEL, css
from ui.ui_helpers import UIHelpers


def _the_code_inside(text):
    """
    The code of a reply that is a JSON object carrying one, the text itself
    for everything else: `{"code": "..."}` streamed verbatim reads as one
    line with `\\n` spelled out in it, and half of it is not JSON at all.
    """
    try:
        payload = json.loads(text)
    except ValueError:
        return text
    return payload["code"] if isinstance(payload, dict) and "code" in payload else text


_PYTHON = re.compile(
    r"(?P<block>\'\'\'[\s\S]*?\'\'\'|\"\"\"[\s\S]*?\"\"\")"
    r"|(?P<comment>#[^\n]*)"
    r"|(?P<string>\'(?:\\.|[^\'\\\n])*\'|\"(?:\\.|[^\"\\\n])*\")"
    r"|(?P<number>\b\d+(?:\.\d*)?(?:[eE][-+]?\d+)?\b)"
    r"|(?P<keyword>\b(?:%s)\b)" % "|".join(keyword.kwlist))


_PAINTED = {"block": "Block comment", "comment": "Comment", "string": "String",
            "number": "Number", "keyword": "Keyword"}


def _as_markup(text):
    """Text that Qt's rich text will show rather than read as markup."""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("\n", "<br>").replace("  ", "&nbsp;&nbsp;"))


def _coloured(code):
    """*code* as rich text, in the colours FreeCAD's editor gives Python."""
    colours = UIHelpers.get_editor_colours()
    written, at = [], 0
    for token in _PYTHON.finditer(code):
        written.append(_as_markup(code[at:token.start()]))
        written.append('<span style="color:%s;">%s</span>'
                       % (colours[_PAINTED[token.lastgroup]], _as_markup(token.group())))
        at = token.end()
    written.append(_as_markup(code[at:]))
    return '<span style="color:%s;">%s</span>' % (colours["Text"], "".join(written))


_TOGGLE_RULE = ("QPushButton { text-align: left; padding: %dpx 0px; border: none; "
                "background: transparent; min-width: 0px; %s }" % (SPACE_XS, css(LABEL)))


class AccordionWidget(QtWidgets.QFrame):
    """
    A toggle row with text that shows and hides beneath it.

    The content is plain text unless the row is a code row, which is rich text
    so that it can be coloured. Everything a code row shows goes through
    `_as_markup` first: a traceback says `<module>`, and QLabel reads that as a
    tag it does not know and drops it.

    A QFrame rather than a plain widget because a plain one paints neither a
    border nor a background from the stylesheet, whatever the rule says.

    The mark is fixed to `ICON` and the title is `Ignored` horizontally, which
    is what makes the row a row. A QLabel left to its own policy took half the
    header's spare width -- 178px of it beside a 16px pixmap -- and the words
    started in the middle of the panel. A QPushButton neither wraps nor
    elides, so a narration long enough to fold was also long enough to push
    the transcript's column past its viewport, and that column does not scroll
    sideways: what did not fit was unreachable rather than scrolled to.
    """

    def __init__(self, title: str = "Thinking...", code: bool = False,
                 boxed: bool = False):
        super().__init__()
        if boxed:
            self.setObjectName("ToolCard")
        layout = QtWidgets.QVBoxLayout(self)
        pad = SPACE_COMPOSER if boxed else 0
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(0)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(SPACE_XS)

        self.mark = QtWidgets.QLabel()
        self.mark.setFixedSize(ICON, ICON)
        self.mark.setVisible(False)
        header.addWidget(self.mark)

        self.toggle_button = QtWidgets.QPushButton(title)
        self.toggle_button.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                         QtWidgets.QSizePolicy.Preferred)
        self.toggle_button.setStyleSheet(_TOGGLE_RULE)
        self.toggle_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.toggle_button.clicked.connect(self.toggle_content)
        header.addWidget(self.toggle_button)
        layout.addLayout(header)

        self.content_area = QtWidgets.QLabel()
        self.content_area.setWordWrap(True)
        self.content_area.setTextFormat(QtCore.Qt.PlainText)
        self.content_area.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        rule = "QLabel { %s %s padding: 0px 0px %dpx %dpx; }"
        self._code_rule = rule % ("font-family: %s;" % UIHelpers.get_monospace_font(),
                                  css(CAPTION), SPACE_SM, SPACE_MD)
        self.content_area.setStyleSheet(
            self._code_rule if code else rule % ("", css(CAPTION), SPACE_SM, SPACE_MD))
        self._code = code
        self.content_area.setVisible(False)
        layout.addWidget(self.content_area)

        self._is_expanded = False
        self._detail = True
        self._buffer = ""
        self._flush_pending = False
        self._anim = None
        self._effect = None
        self._clock = None
        self._since = 0.0
        self._title = title
        self._paint_chevron()

    def append_text(self, text: str):
        """
        Appends by straight concatenation: reasoning arrives as fragments and
        carries its own newlines, so a paragraph break per call would break a
        streamed sentence across a dozen lines.

        `content_area` has word-wrap on, so `setText` relays out everything
        accumulated so far, not just the new fragment. Buffering here and
        flushing once per event-loop turn (`QTimer.singleShot(0, ...)`)
        coalesces a whole burst of streamed deltas into one relayout instead
        of one per delta.
        """
        self._buffer += text
        if not self._flush_pending:
            self._flush_pending = True
            QtCore.QTimer.singleShot(0, self._flush)
        if not self._detail:
            self._detail = True
            self.setCursor(QtCore.Qt.ArrowCursor)
            self.toggle_button.setCursor(QtCore.Qt.PointingHandCursor)
            self._paint_chevron()

    def _flush(self):
        """
        A row can be gone by the time its flush fires -- the transcript
        widget it lived in was cleared or replaced -- so `setText` on an
        already-deleted QLabel is swallowed rather than left to abort the
        rest of this event-loop pass, which is what one dead row otherwise
        does to every other flush queued behind it.
        """
        self._flush_pending = False
        shown = _the_code_inside(self._buffer)
        try:
            if shown != self._buffer:
                self.content_area.setStyleSheet(self._code_rule)
                self._code = True
            if self._code:
                self.content_area.setTextFormat(QtCore.Qt.RichText)
                self.content_area.setText(_coloured(shown))
            else:
                self.content_area.setText(shown)
        except RuntimeError:
            pass

    def opens_onto_nothing(self):
        """
        Closes the row and takes its disclosure mark away until there is
        something under it.

        A chevron is a promise that a click shows more, and the one route this
        panel runs on withholds the words of its reasoning: the row still has
        to go up, since a phase that says nothing for four minutes reads as a
        run that has died, but it must not offer to open onto an empty label.
        `append_text` gives the mark back at the first word that does arrive.
        """
        self._detail = False
        self._is_expanded = False
        self.content_area.setVisible(False)
        self.toggle_button.setIcon(QtGui.QIcon())
        self.toggle_button.setCursor(QtCore.Qt.ArrowCursor)

    def mark_as_reasoning(self):
        """
        The brain Figma's Reasoning header carries, and text that reads
        subdued next to an answer.

        Subdued the way `HistoryItem` dims its timestamp: an opacity effect
        rather than a colour of its own, since the theme still decides what
        the text is (ADR-0011, Figma's Chain of Thought component).

        The brain is `ICON` and the chevron beside it `ICON_DENSE`, which is
        what those two names are for: 16 is the mark that says what the row is
        and sits beside its words, 12 is the disclosure mark inline in them.
        The transcript carries one other kind of row that opens, and telling a
        phase of reasoning from a call the run made has to survive a glance
        down a column of them.
        """
        self.mark.setPixmap(
            UIHelpers.get_icon("brain", size=ICON, on=self.toggle_button)
            .pixmap(ICON, ICON))
        self.mark.setVisible(True)
        effect = QtWidgets.QGraphicsOpacityEffect(self.content_area)
        effect.setOpacity(0.6)
        self.content_area.setGraphicsEffect(effect)

    def start_pulsing(self):
        """
        The 'still working' signal — the one animation worth keeping — and,
        beside the title, how long it has been working.

        A pulse says the run is alive; it does not say whether this has been
        going for ten seconds or for six minutes, and that is the question
        the user asks a panel that has said nothing for a while. Nothing on
        any route can say how long the model will take (no provider reports a
        budget or a remaining time mid-stream), so the panel says the one
        thing that is true and useful: how long it has already been. The
        count stays on the row once it stops, because "that phase took four
        minutes" is worth keeping in the transcript.
        """
        self._title = self.toggle_button.text()
        self._since = time.monotonic()
        self._clock = QtCore.QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._say_how_long)
        self._clock.start()
        self._effect = QtWidgets.QGraphicsOpacityEffect(self.toggle_button)
        self.toggle_button.setGraphicsEffect(self._effect)
        self._anim = QtCore.QPropertyAnimation(self._effect, b"opacity")
        self._anim.setDuration(1400)
        self._anim.setKeyValueAt(0, 1.0)
        self._anim.setKeyValueAt(0.5, 0.45)
        self._anim.setKeyValueAt(1, 1.0)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QtCore.QEasingCurve.InOutSine)
        self._anim.start()

    def _say_how_long(self):
        seconds = int(time.monotonic() - self._since)
        self.toggle_button.setText(
            "%s  ·  %d:%02d" % (self._title, seconds // 60, seconds % 60))

    def stop_pulsing(self):
        if self._clock:
            self._say_how_long()
            self._clock.stop()
            self._clock = None
        if self._anim:
            self._anim.stop()
            self._anim = None
        if self._effect:
            self._effect.setOpacity(1.0)
            self.toggle_button.setGraphicsEffect(None)
            self._effect = None

    def toggle_content(self):
        if not self._detail:
            return
        self._is_expanded = not self._is_expanded
        self.content_area.setVisible(self._is_expanded)
        self._paint_chevron()

    def _paint_chevron(self):
        """
        The disclosure mark, from the icon set rather than from the two
        triangles the font happens to carry.

        `chevron_down` is drawn once and rotated for the closed state: the set
        has the one shape, and a glyph is not a shape -- it is whatever the
        user's font decided, at whatever weight, and it does not take the
        icon tint the rest of the panel is drawn with.
        """
        icon = UIHelpers.get_icon("chevron_down", size=ICON_DENSE, on=self.toggle_button)
        if not self._is_expanded:
            pixmap = icon.pixmap(ICON_DENSE, ICON_DENSE).transformed(
                QtGui.QTransform().rotate(-90), QtCore.Qt.SmoothTransformation)
            icon = QtGui.QIcon(pixmap)
        self.toggle_button.setIcon(icon)
