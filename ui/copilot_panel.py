"""
The panel: a renderer of the event stream.

It does not drive the loop. It sends one Intent, then renders whatever the
loop emits — `CopilotPanel.on_event` is a switch over event kinds and nothing
else. That is what lets the same stream be the session log, and what keeps a
frozen UI impossible: the loop runs on a worker thread and only ever emits.

The panel has no colours. FreeCAD paints its UI with one global stylesheet and
leaves QPalette at whatever the OS handed over, so `palette(base)` was a
light-theme value painted over a dark theme (ADR-0011). What is left in the
stylesheet is geometry and typography, which no theme has an opinion about.

`RESTORE_CHUNK` is how many events are replayed per event-loop turn when a
Session is opened: small enough that no single turn is felt, large enough that
a short Session lands in one. `ATTACHMENT_WIDTH` is how wide an attached
picture is shown in the chat: wide enough to recognise the drawing, narrow
enough that it is not the whole transcript. `OPEN_AT_MOST` is how many entries
a list beside the Plan may hold and still stand open — see `_render_plan`.
`FOLDS_OVER` is how long a narrated line may be before it folds into a row of
its own, and `TITLE_KEPT` how much of its first sentence titles that row — see
`_say`.
"""
import threading

import FreeCAD

try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from .chat_widgets        import TextMessageWidget, AssistantMessageWidget
from .multi_tab_manager   import ChatSessionManager
from .upload_widget       import FileChip, UploadWidget, FileChipBar
from .accordion_widget    import AccordionWidget
from .plan_widget         import PlanWidget, DONE as PLAN_DONE, FAILED as PLAN_FAILED
from .queue_widget        import QueueWidget
from .review_verdict_widget import ReviewVerdictWidget
from .assertion_results_widget import AssertionResultsWidget, summary_line
from ui.design_tokens     import (CONTROL, CONTROL_DENSE, HAIRLINE, ICON, ICON_DENSE, RADIUS_CONTROL,
                                  RADIUS_SURFACE, RUNNING, SPACE_COMPOSER, SPACE_MD,
                                  SPACE_SM, SPACE_XS, WASH)
from ui.typography        import BODY, BODY_STRONG, CAPTION, LABEL, css
from utils import settings_manager
from utils import provider_manager
from ui.ui_helpers import UIHelpers


def _build_qss() -> str:
    """
    The panel stylesheet: geometry and typography, and nothing else.

    Every colour that used to be here asked `palette(...)` for it, and under
    FreeCAD's dark theme the palette still holds the OS's light values — so the
    input card was a white rectangle in a dark panel (ADR-0011). Leaving colour out
    hands these widgets to FreeCAD's global stylesheet, including themes that
    did not exist when this was written.

    The rules follow AI Elements' own grammar (`docs/research/fcc169-ai-elements-visual-grammar.md`,
    read off the user's Figma file). Three of its rules decide most of what is
    below. Separation takes the cheapest form that still reads: nothing, then a
    one-pixel hairline with no fill, then a wash, and a filled surface only for
    the handful of things meant to be taken for a panel of their own — the
    composer, the Plan, a results card. State is never a repaint: a control
    keeps its fill and swaps its icon, which is why nothing here has a
    `:disabled` or an error colour. And exactly one control on screen carries
    the accent fill — Send, or the primary answer to whatever is being asked.

    Where the keyboard is is the one exception, and it is not a state of the
    control: a field that does not say it has focus is a field the user cannot
    tell they are typing into. It is drawn in the accent, since that is what
    the accent is for, and on the hairline the composer already has, so it
    costs no layout.

    Where a rule pins a dimension it also resets padding and border: Qt's
    min/max-width size the content rect, and the theme's padding is added on
    top of them.
    """
    return f"""
        /* Panel root */
        QWidget {{
            font-family: {UIHelpers.get_ui_font()};
            {css(BODY)}
        }}

        QDockWidget::title {{ padding: 0px; height: 0px; }}

        /* The header, ruled off from the transcript under it */
        QFrame#HeaderBar {{
            border: none;
            border-bottom: 1px solid {HAIRLINE};
        }}

        QLabel#HeaderTitle {{ {css(BODY_STRONG)} }}

        /* Icon-only actions: one hit-target size, a wash under the pointer,
           and no other state. */
        QPushButton#IconButton {{
            background: transparent;
            border: none;
            border-radius: {RADIUS_CONTROL}px;
            padding: 0px;
            min-width: {CONTROL}px; max-width: {CONTROL}px;
            min-height: {CONTROL}px; max-height: {CONTROL}px;
        }}

        QPushButton#IconButton:hover {{ background-color: {WASH}; }}

        /* Usage/cost running total */
        QLabel#UsageTotal {{
            {css(CAPTION)}
            padding: 0px {SPACE_XS}px;
        }}

        /* The empty chat still tells the user where they stand */
        QLabel#EmptyState {{
            {css(CAPTION)}
            padding: {SPACE_MD}px;
        }}

        /* The composer: a surface, so a fill and a hairline both, and the one
           twelve in the spacing scale around the text. */
        QFrame#InputContainer {{
            background-color: {WASH};
            border: 1px solid {HAIRLINE};
            border-radius: {RADIUS_SURFACE}px;
            margin: 0px {SPACE_MD}px {SPACE_MD}px {SPACE_MD}px;
        }}

        /* Which of the panel's fields the keyboard is in. The hairline is
           already there, so the ring costs no layout: the same one pixel,
           repainted in the accent. */
        QFrame#InputContainer[focused="true"] {{
            border: 1px solid {RUNNING};
        }}

        QTextEdit#ChatInput {{
            border: none;
            background: transparent;
            {css(BODY)}
            padding: {SPACE_COMPOSER}px;
        }}

        /* The Queue, pinned above the composer: a hairline and no fill --
           the fills belong to what the reader stops on, and this is read at a
           glance rather than stopped on. */
        QFrame#QueueCard {{
            border: 1px solid {HAIRLINE};
            border-radius: {RADIUS_SURFACE}px;
            margin: 0px {SPACE_MD}px {SPACE_SM}px {SPACE_MD}px;
        }}

        QFrame#QueueRow {{ border-radius: {RADIUS_CONTROL}px; }}

        QFrame#QueueRow:hover {{ background-color: {WASH}; }}

        /* What is being asked is asked in the open: the Confirmation this
           follows carries no card at all, and the accent on its answer is
           what marks it out. */
        QFrame#GateCard {{ border: none; }}

        /* The Plan, editable until Build locks it (FCC-177): the one thing
           on screen at the Gate that is read as an object, so a surface. */
        QFrame#PlanCard {{
            background-color: {WASH};
            border: 1px solid {HAIRLINE};
            border-radius: {RADIUS_SURFACE}px;
        }}

        QListWidget#PlanSteps {{
            border: none;
            background: transparent;
        }}

        QListWidget#PlanSteps::item {{
            padding: {SPACE_XS}px {ICON + SPACE_SM}px {SPACE_XS}px {SPACE_SM}px;
            border-radius: {RADIUS_CONTROL}px;
        }}

        QListWidget#PlanSteps::item:selected {{
            background-color: {WASH};
        }}

        QPushButton#PlanAddStep {{
            border: none;
            background: transparent;
            text-align: left;
            padding: {SPACE_XS}px {SPACE_SM}px;
            min-width: 0px;
            {css(CAPTION)}
        }}

        /* An action offered beside a primary one: a label, a hairline, and no
           fill of its own. */
        QPushButton#PillButton {{
            border: 1px solid {HAIRLINE};
            border-radius: {RADIUS_CONTROL}px;
            padding: 0px {SPACE_SM}px;
            min-width: 0px;
            {css(LABEL)}
        }}

        QPushButton#PillButton:hover {{ background-color: {WASH}; }}

        /* A control that is picked between rather than pressed: the composer's
           strip, where the attach button sets the idiom -- no edge, no fill,
           and a wash under the pointer. An outline here would fence off what
           is a current value rather than a thing to press. */
        QPushButton#PickerButton {{
            background: transparent;
            border: none;
            border-radius: {RADIUS_CONTROL}px;
            padding: 0px {SPACE_XS}px;
            min-width: 0px;
            {css(LABEL)}
        }}

        QPushButton#PickerButton:hover {{ background-color: {WASH}; }}

        /* The Review verdict card (FCC-178) and the acceptance checks
           (FCC-179): both are results, and a result is a surface. */
        QFrame#ReviewVerdictCard, QFrame#AssertionResultsCard {{
            background-color: {WASH};
            border: 1px solid {HAIRLINE};
            border-radius: {RADIUS_SURFACE}px;
        }}

        /* A call the run made, in the shape Figma's Tool card takes: a
           hairline, no fill, and the control radius rather than a surface's. */
        QFrame#ToolCard {{
            border: 1px solid {HAIRLINE};
            border-radius: {RADIUS_CONTROL}px;
        }}

        /* The one accent-filled control on screen -- Send, or the answer the
           card being shown is asking for. The glyph takes the colour the
           theme pairs with that fill; left at the ordinary foreground it is
           the one contrast a theme guarantees, broken by hand. */
        QPushButton#PrimaryButton, QPushButton#ReviewApprove {{
            background-color: {RUNNING};
            color: palette(highlighted-text);
            border: none;
            border-radius: {RADIUS_CONTROL}px;
            padding: {SPACE_SM}px {SPACE_MD}px;
            min-width: 0px;
            {css(LABEL)}
        }}

        /* Send and Stop: the glyph alone, in the idiom the attach button and
           the pickers beside them already use. A filled pill here was the one
           thing in the composer's strip wearing a colour, and it read as a
           second primary action next to the Plan Gate's. */
        QPushButton#SendButton {{
            background: transparent;
            border: none;
            border-radius: {RADIUS_CONTROL}px;
            padding: 0px;
            min-width: {CONTROL}px; max-width: {CONTROL}px;
            min-height: {CONTROL}px; max-height: {CONTROL}px;
        }}

        QPushButton#SendButton:hover {{ background-color: {WASH}; }}

        /* Scrollbars */
        QScrollBar:vertical {{
            border: none; background: transparent; width: 6px; margin: 0;
        }}
        QScrollBar::handle:vertical {{
            min-height: 20px; border-radius: 3px;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    """


def _format_cost(usd: float) -> str:
    """
    The running total, in dollars.

    `core.events.COST` carries `usd` and `model` -- what the call actually
    cost, already converted -- and nothing that counts tokens; the CLI route
    reads `total_cost_usd` straight off the provider (`core/llm.py`) and the
    keyed route emits no `cost` event at all (`core/loop.py :: _streamed`
    reads `last_cost`, which `LiteLLMClient` never sets). A token count beside
    the dollar figure would be invented rather than shown, so the total is
    dollars alone (FCC-176).

    Two decimal places round every call under a cent to `$0.00`; four is
    enough of a call like that to still read as spent rather than free.
    """
    if usd and usd < 0.01:
        return "$%.4f" % usd
    return "$%.2f" % usd


def _format_elapsed(seconds: float) -> str:
    """
    Wall-clock duration, zero-padded to M:SS when under an hour, H:MM:SS when over.

    90 seconds -> "1:30"
    3660 seconds -> "1:01:00"
    45 seconds -> "0:45"
    """
    if seconds < 0:
        seconds = 0

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    else:
        return f"{minutes}:{secs:02d}"


def _folded(line: str) -> "QtWidgets.QWidget":
    """
    *line* as a closed row titled by its first sentence.

    The title is a sentence rather than a label of our own, because what is
    being folded is prose the run wrote and the panel cannot summarise: the
    first sentence is the one part of it written to be read first.
    """
    head = line.strip().split(". ")[0].split("\n")[0].strip()
    row = AccordionWidget(head[:TITLE_KEPT] + ("…" if len(head) > TITLE_KEPT else ""))
    row.append_text(line)
    return row


def _beside_the_intent(widget) -> "QtWidgets.QWidget":
    """
    *widget* pushed to the right margin, where the user's own turn stands.

    What the run was shown is part of what the user said, so an attachment
    lines up under their bubble rather than at the left margin the run
    answers from.
    """
    row = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addStretch(1)
    layout.addWidget(widget)
    return row


def _at_the_left_margin(widget) -> "QtWidgets.QWidget":
    """
    *widget* held to the left margin, where the run's own turn stands.

    A chip sizes itself to its name, and a layout given one with nothing beside
    it centres it in the column; the stretch on the right is what keeps it
    lined up with the sentence above it.
    """
    row = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(widget)
    layout.addStretch(1)
    return row



class _PasteAwareTextEdit(QtWidgets.QTextEdit):
    """The composer: Enter sends, Shift+Enter inserts a newline, Ctrl+V images
    go to the UploadWidget.

    A pasted image is consumed here rather than handed on, so its raw bytes
    never land in the text field.

    `focus_changed` is how the card around it knows to draw its focus ring.
    The ring belongs on the card, not on this widget -- the border and the
    fill the user reads as "the composer" are the card's -- and Qt has no
    `:focus-within`, so the field that actually takes the keyboard has to say
    so out loud."""

    paste_image_requested = QtCore.Signal()
    send_requested = QtCore.Signal()
    focus_changed = QtCore.Signal(bool)

    def focusInEvent(self, event: QtGui.QFocusEvent):
        super().focusInEvent(event)
        self.focus_changed.emit(True)

    def focusOutEvent(self, event: QtGui.QFocusEvent):
        super().focusOutEvent(event)
        self.focus_changed.emit(False)

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        if event.key() == QtCore.Qt.Key_V and (event.modifiers() & QtCore.Qt.ControlModifier):
            mime = QtWidgets.QApplication.clipboard().mimeData()
            if mime.hasImage():
                self.paste_image_requested.emit()
                return

        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter) \
                and not (event.modifiers() & QtCore.Qt.ShiftModifier):
            self.send_requested.emit()
            return
        super().keyPressEvent(event)


class EventBridge(QtCore.QObject):
    """
    Carries loop events from the worker thread to the GUI thread.

    The loop knows nothing about Qt: it emits into an EventStream, this
    subscribes, and Qt's queued connection does the thread hop.
    """

    event = QtCore.Signal(object)

    def subscriber(self):
        return self.event.emit


class AskTheUser(QtCore.QObject):
    """
    Blocks the worker thread until the user answers, on the GUI thread.

    Four questions go through here: the Plan Gate, the Owned Object rule, the
    values Plan is missing, and what to do about a result that did not pass.
    All are asked from the loop's thread and must be answered by a person, so
    the signal crosses to the GUI thread and the worker waits on an Event.
    """

    plan_gate = QtCore.Signal(object)
    owned_objects = QtCore.Signal(object)
    missing_values = QtCore.Signal(object)
    revision = QtCore.Signal(object)

    def __init__(self):
        super().__init__()
        self._answered = threading.Event()
        self._answer = None

    def about_the_plan(self, steps):
        return self._ask(self.plan_gate, steps)

    def about_objects_we_do_not_own(self, kinds):
        """
        Whether the assistant may delete something the user made themselves.

        `abandon` releases this with an Approval, like every other waiting
        question, and an Approval is truthy — so what makes Stop a refusal
        rather than consent is `ApplyPipeline`, which takes nothing but an
        explicit yes. It is the choke point every asker routes through, so the
        guard is there rather than repeated in each of them.
        """
        return self._ask(self.owned_objects, kinds)

    def about_missing_values(self, question):
        """
        The values Plan has not been given. An empty answer means "you choose".

        `abandon` releases every waiting question with an Approval, which is
        the Plan Gate's answer and not one of these — anything that is not
        text is nobody answering.
        """
        answer = self._ask(self.missing_values, question)
        return answer if isinstance(answer, str) else ""

    def about_the_revision(self, failure):
        """
        What to do about a result that did not pass, before anything is wiped.

        `abandon` releases every waiting question with an Approval, which is
        not one of these three answers, so Stop falls to the default. That is
        deliberately not "accept": the loop asks `cancelled()` before it plans
        anything, so a stopped run is recorded as stopped rather than as a
        result the user took (FCC-77).
        """
        from core.loop import DECISIONS, REVISE

        answer = self._ask(self.revision, failure)
        return answer if answer in DECISIONS else REVISE

    def _ask(self, signal, payload):
        """
        Emit the question, then block until someone answers it.

        A receiver in this object's own thread answers inside `emit`, so the
        Event is already set and the wait returns at once. A receiver anywhere
        else answers later, over a queued connection, and the wait is what
        holds the worker until it does.

        The third case is the dangerous one: asked from this object's own
        thread with nobody to answer inline. The queued answer can only be
        delivered by an event loop this very thread is now blocked out of, so
        `wait()` never returns and the process deadlocks with no traceback and
        no CPU to show for it. Refusing to wait names the fault instead.
        """
        self._answered.clear()
        self._answer = None
        signal.emit(payload)
        if not self._answered.is_set() and QtCore.QThread.currentThread() == self.thread():
            raise RuntimeError(
                "%s was asked on the thread that has to answer it; "
                "no event loop can deliver the answer." % signal
            )
        self._answered.wait()
        return self._answer

    def answer(self, value):
        self._answer = value
        self._answered.set()

    def abandon(self):
        """Release a waiting question so Stop is not swallowed by a prompt."""
        if not self._answered.is_set():
            from core.loop import REJECT, Approval

            self._answer = Approval(REJECT)
            self._answered.set()


class LoopWorker(QtCore.QObject):
    """
    Runs one Intent through the loop on a worker thread.

    It never reports progress itself — the event stream does that. All this
    signals is that the run is over.

    `finished` carries the transcript the run belongs to, so the panel can
    connect a bound method of its own rather than a lambda closing over it.
    This object lives on the worker thread, and Qt uses the *sender* as the
    context of a connection made to a plain callable — so an `AutoConnection`
    to a lambda compares the worker thread against itself, resolves to a
    direct call, and runs the panel's slot on the worker thread. That is a
    widget tree and a QTimer built off the GUI thread, measured as sixteen
    `QObject::setParent: ... different thread` warnings, one
    `QObject::killTimer: ... another thread`, and a segfault inside
    `queue_widget.py :: _Elided.set_full`. A bound method of a main-thread
    QObject gives Qt that QObject as the context instead, which is what makes
    the hop queued. `Qt.QueuedConnection` on the lambda does not help: it
    still posts to the context's thread, and the context is still this object.

    The client is held rather than built per run, and is dropped so that the
    next run rebuilds it from settings when the model changes.
    """

    finished = QtCore.Signal(object, str)
    document_named = QtCore.Signal(str, str, str)

    def __init__(self, bridge: EventBridge, ask: AskTheUser):
        super().__init__()
        self.bridge = bridge
        self.ask = ask
        self.client = None
        self.pipeline = None
        self.document = None
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()
        self.ask.abandon()

    def _get_client(self):
        if self.client is None:
            from core.llm import client_from_settings
            self.client = client_from_settings(settings_manager.instance().get_last_model())
        return self.client

    def _get_tools(self, events=None):
        """
        The registry for one run.

        The pipeline outlives a run — it keeps the Session's Owned Objects — so
        the stream it narrates the Owned Object question into is set per run
        rather than at construction.

        `self.document` is this Session's own bound Document (FCC-181), set by
        the panel before the first Intent is sent and reused for every Intent
        after that — a later focus change elsewhere must not redirect a Tool
        call or an Apply that belongs to this Session.
        """
        from core.apply import ApplyPipeline
        from core.freecad_tools import build_registry

        if self.pipeline is None:
            self.pipeline = ApplyPipeline(document=self.document,
                                          confirm=self.ask.about_objects_we_do_not_own)
        self.pipeline.events = events
        return build_registry(document=self.document, pipeline=self.pipeline)

    @QtCore.Slot(str, object, str, object)
    def do_work(self, intent: str, attachments: list, transcript: str,
                outstanding: list = None):
        """
        Runs one Intent and emits the result.

        A missing transcript log must not cost the user their run, and neither
        must a Session that cannot be named after its Document, so both are
        allowed to fail quietly. A model that cannot be resolved is a known
        outcome and belongs in the transcript as a sentence, not as a traceback
        the user cannot act on.
        """
        from core import events as ev
        from core.events import EventStream
        from core.loop import run
        from utils.logger import generate_crash_report, logger

        self._stop.clear()
        events = EventStream([self.bridge.subscriber()])
        try:
            events.log_to(transcript)
        except Exception:
            pass

        events.subscribe(lambda event: logger.error("%s: %s" % (event.kind, event.data))
                         if event.kind in (ev.BLOCKED, ev.ERROR) else None)

        tools = self._get_tools(events)
        try:
            self.document_named.emit(transcript,
                                     str(tools.call("snapshot").get("document") or ""),
                                     str(getattr(self.document, "Uid", "") or ""))
        except Exception:
            pass

        try:
            client = self._get_client()
        except ValueError as exc:
            events.emit(ev.ERROR, message=str(exc))
            self.finished.emit(None, transcript)
            return

        try:
            result = run(intent, llm=client, tools=tools,
                         events=events, cancelled=self._stop.is_set,
                         attachments=attachments, gate=self.ask.about_the_plan,
                         ask=self.ask.about_missing_values,
                         decide=self.ask.about_the_revision,
                         outstanding=list(outstanding or ()))
            self.finished.emit(result, transcript)
        except Exception as exc:
            events.emit("error", message=generate_crash_report(exc, "LoopWorker.do_work()"))
            self.finished.emit(None, transcript)


class _TitleThread(QtCore.QThread):
    """One short call naming a Session, off the UI thread so the run is not held up."""

    titled = QtCore.Signal(str)

    def __init__(self, intent: str, model_id: str, parent=None):
        super().__init__(parent)
        self._intent = intent
        self._model_id = model_id

    def run(self):
        from core.session_title import generate

        self.titled.emit(generate(self._intent, self._model_id))


RESTORE_CHUNK = 25

ATTACHMENT_WIDTH = 320

OPEN_AT_MOST = 5

FOLDS_OVER = 400

TITLE_KEPT = 70

PICKER_SHARE = 0.35


class _SessionWorker(QtCore.QObject):
    """
    One Session's own `LoopWorker`, on its own `QThread` (FCC-181).

    Standing up one of these per Session — instead of the single global worker
    this panel used to hold — is what makes two open Documents genuinely
    parallel: each Session's Tool calls and Apply are bound to its own
    `worker.document` (set once, from whichever Document is in focus when the
    Session's first Intent is sent) rather than to `FreeCAD.ActiveDocument` at
    the moment of the call, which is whichever Document the user happens to be
    looking at right then. A Preflight Worker comes along for free: it lives on
    `worker.pipeline`, and that pipeline is this Session's own.

    `running`, `outstanding` and `sent_attachments` are what used to be the
    panel's own single set of instance attributes — carried here instead, so a
    Session backgrounded mid-run keeps its own state rather than sharing the
    one the panel happens to be looking at. `dirty` is set when an event lands
    for this Session while it is not the one on screen, so the panel knows to
    resync from the transcript rather than trust stale widgets the next time it
    is shown. `pending_question` is the one live Ask this Session is blocked
    on, if any, so switching back to a Session parked at its Plan Gate puts the
    same card up again instead of losing it.
    """

    request_work = QtCore.Signal(str, object, str, object)

    def __init__(self, bridge: EventBridge, ask: AskTheUser):
        super().__init__()
        self.bridge = bridge
        self.ask = ask
        self.thread = QtCore.QThread()
        self.worker = LoopWorker(bridge, ask)
        self.worker.moveToThread(self.thread)
        self.request_work.connect(self.worker.do_work)
        self.running = False
        self.outstanding: list = []
        self.sent_attachments: list = []
        self.dirty = False
        self.pending_question = None
        self.thread.start()

    def shut_down(self, grace_ms: int):
        """Stop the run, whatever it is in the middle of, and end the thread."""
        self.worker.stop()
        if self.worker.pipeline is not None:
            self.worker.pipeline.shutdown()
        if self.thread.isRunning():
            self.thread.quit()
            if not self.thread.wait(grace_ms):
                self.thread.terminate()
                self.thread.wait(1000)


class CopilotPanel(QtWidgets.QDockWidget):
    """
    The dock: the panel's widgets, its worker thread, and the event stream
    rendered into a transcript.

    The dock carries an object name because QMainWindow::saveState() drops
    unnamed docks, and without one the panel's position is never restored
    across restarts.

    Every launch opens an empty Session; the earlier ones stay in the picker
    with their transcripts intact.

    The status line sits outside the transcript's scroll area, so its minimum
    width is the panel's: a Step description it cannot break — one long token,
    or wrapping off entirely — widens the dock, and Qt does not give the width
    back when the text goes short again.

    One `_SessionWorker` per Session (FCC-181), in `self._workers`, keyed by
    the Session's own log path — the same stable identity `name_document` and
    `title_arrived` already key by, since a position shifts under pruning and a
    log does not. `worker`, `thread`, `bridge` and `ask` stay as properties
    resolving to whichever Session is currently on screen, so the rest of this
    class — and the tests that already knew a panel had one worker — still
    reads that way; what changed is that switching sessions now switches which
    `_SessionWorker` those names resolve to, instead of resetting the one
    worker everyone shared. `outstanding`, the acceptance checks a run left
    wrong to carry into that Session's next Intent, lives on the
    `_SessionWorker` for the same reason a Document does — it belongs to the
    Session, not to whatever happens to be in front.
    """

    request_work = QtCore.Signal(str, object, str, object)

    def __init__(self):
        super().__init__("FreeCAD Copilot")
        self.setObjectName("FreeCadCopilotPanel")
        self.setStyleSheet(_build_qss())

        root = QtWidgets.QWidget()
        root.setObjectName("MainContainer")
        self.setWidget(root)

        root_layout = QtWidgets.QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = self._build_header()
        root_layout.addWidget(header)

        self.chat_sessions = ChatSessionManager()
        self.chat_sessions.setObjectName("ChatArea")
        self.chat_sessions.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        root_layout.addWidget(self.chat_sessions, stretch=1)

        from .history_popup import HistoryPopup
        self.history_popup = HistoryPopup(self)
        self.history_popup.session_selected.connect(self.switch_session)
        self.history_popup.session_deleted.connect(self.delete_session)

        self._queue_widget = QueueWidget()
        root_layout.addWidget(self._queue_widget)

        input_card = self._build_input_card()
        root_layout.addWidget(input_card)

        self._workers: dict[str, _SessionWorker] = {}

        self._running = False
        self._empty_state = None
        self._title_threads = []
        self._reset_render_state()

        FreeCAD.addDocumentObserver(self)

        app = QtWidgets.QApplication.instance()
        if app:
            app.aboutToQuit.connect(self._cleanup_thread)

        self.chat_sessions.session_changed.connect(self.update_header_title)
        self.chat_sessions.session_removed.connect(lambda _: self.update_header_title(self.chat_sessions.stack.currentIndex()))

        self.chat_sessions.load()
        self.new_chat()

    @property
    def worker(self) -> LoopWorker:
        return self._worker_for(self._current_log()).worker

    @property
    def thread(self) -> QtCore.QThread:
        return self._worker_for(self._current_log()).thread

    @property
    def bridge(self) -> EventBridge:
        return self._worker_for(self._current_log()).bridge

    @property
    def ask(self) -> AskTheUser:
        return self._worker_for(self._current_log()).ask

    def _current_log(self):
        info = self.chat_sessions.get_session_info(self.chat_sessions.stack.currentIndex())
        return info["log"] if info else None

    def _worker_for(self, log: str) -> _SessionWorker:
        """
        This Session's own worker, building it the first time it is asked for.

        Built lazily rather than one per Session up front: most Sessions in the
        picker never run again, and a QThread nobody uses is a thread this
        panel would still have to stop on exit.

        Which of these may close over *log* in a lambda is decided by where
        the sender lives, not by what the slot does. `bridge` and `ask` are
        built here and stay on the GUI thread, so Qt — which takes the sender
        as the context of a connection made to a plain callable — still sees
        two different threads at emit time and queues the hop. Only `worker`
        is moved onto the QThread (`_SessionWorker.__init__`), so a lambda on
        one of its signals is a direct call on that thread; its two go to
        bound methods of this panel, and `finished` carries the transcript so
        that it can.
        """
        bundle = self._workers.get(log)
        if bundle is None:
            bundle = _SessionWorker(EventBridge(), AskTheUser())
            bundle.worker.finished.connect(self.on_run_finished)
            bundle.worker.document_named.connect(self.on_document_named)
            bundle.bridge.event.connect(lambda event, log=log: self._route_event(log, event))
            bundle.ask.plan_gate.connect(
                lambda payload, log=log: self._route_ask(log, "plan_gate", payload))
            bundle.ask.owned_objects.connect(
                lambda payload, log=log: self._route_ask(log, "owned_objects", payload))
            bundle.ask.missing_values.connect(
                lambda payload, log=log: self._route_ask(log, "missing_values", payload))
            bundle.ask.revision.connect(
                lambda payload, log=log: self._route_ask(log, "revision", payload))
            self._workers[log] = bundle
        return bundle

    def _bind_document(self, bundle: _SessionWorker):
        """
        Bind this Session's worker to whichever Document is in focus, once.

        Read here rather than left for `build_registry`'s own fallback: this is
        the one moment — the Session's first Intent — that "whichever Document
        is in focus" and "the Document this Session is about" are guaranteed to
        be the same Document (FCC-181). Every Intent after this one reuses the
        binding regardless of what the user brings to front meanwhile.

        Goes through `core.freecad_tools.active_document` rather than
        `FreeCAD.ActiveDocument` directly — the panel reaching the Document on
        its own is exactly what `test_registry_is_the_only_route.py` exists to
        catch.
        """
        if bundle.worker.document is None:
            from core.freecad_tools import active_document
            bundle.worker.document = active_document()

    def _route_event(self, log: str, event):
        """
        An event from *log*'s own worker: render it if that Session is on
        screen, otherwise just remember it happened.

        The event already reached the Session's own transcript on disk —
        `LoopWorker.do_work` logs to it directly — so nothing here is lost by
        not rendering it; `switch_session` resyncs from that transcript the
        next time this Session is shown.
        """
        if log == self._current_log():
            self.on_event(event)
            return
        bundle = self._workers.get(log)
        if bundle:
            bundle.dirty = True

    def _route_ask(self, log: str, kind: str, payload):
        """
        A question from *log*'s own worker: put the card up if that Session is
        on screen, otherwise hold it until the user switches back to it.

        Recorded whichever way, not only while backgrounded: the panel resets
        its render state on every switch, so a card left open when the user
        looks away is gone from screen the instant they do, and has to be put
        back up rather than lost, the same as one that arrived while away.
        """
        bundle = self._workers.get(log)
        if bundle:
            bundle.pending_question = (kind, payload)
        if log == self._current_log():
            self._dispatch_ask(kind, payload)

    def _dispatch_ask(self, kind: str, payload):
        {"plan_gate": self.on_plan_gate,
         "owned_objects": self.on_owned_objects,
         "missing_values": self.on_missing_values,
         "revision": self.on_revision}[kind](payload)

    def _build_header(self):
        bar = QtWidgets.QFrame()
        bar.setObjectName("HeaderBar")
        bar.setFixedHeight(44)

        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(SPACE_MD, SPACE_SM, SPACE_SM, SPACE_SM)
        layout.setSpacing(SPACE_XS)

        self.title_label = QtWidgets.QLabel("New Chat")
        self.title_label.setObjectName("HeaderTitle")
        self.title_label.setToolTip("Double-click to rename this session")
        self.title_label.setCursor(QtCore.Qt.IBeamCursor)
        self.title_label.mouseDoubleClickEvent = lambda event: self.rename_current_session()
        layout.addWidget(self.title_label)

        layout.addStretch()

        self.usage_label = QtWidgets.QLabel("$0.00  0:00")
        self.usage_label.setObjectName("UsageTotal")
        layout.addWidget(self.usage_label)

        self.new_btn = QtWidgets.QPushButton()
        self.new_btn.setObjectName("IconButton")
        self.new_btn.setFixedSize(CONTROL, CONTROL)
        self.new_btn.setToolTip("New chat session")
        self.new_btn.setIcon(UIHelpers.get_icon("plus", size=ICON, on=self.new_btn))
        self.new_btn.clicked.connect(self.new_chat)
        layout.addWidget(self.new_btn)

        self.history_btn = QtWidgets.QPushButton()
        self.history_btn.setObjectName("IconButton")
        self.history_btn.setFixedSize(CONTROL, CONTROL)
        self.history_btn.setToolTip("Chat history")
        self.history_btn.setIcon(UIHelpers.get_icon("history", size=ICON, on=self.history_btn))
        self.history_btn.clicked.connect(self.toggle_history)
        layout.addWidget(self.history_btn)

        return bar

    def _build_input_card(self) -> QtWidgets.QFrame:
        card = QtWidgets.QFrame()
        card.setObjectName("InputContainer")
        card.setProperty("focused", "false")
        self._input_card = card

        inner = QtWidgets.QVBoxLayout(card)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        self.chip_bar = FileChipBar()
        inner.addWidget(self.chip_bar)

        self.user_input = _PasteAwareTextEdit()
        self.user_input.setObjectName("ChatInput")
        self.user_input.setPlaceholderText("Describe what you want to build…")
        self.user_input.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.user_input.paste_image_requested.connect(
            lambda: self.upload_widget.handle_clipboard_paste()
        )
        self.user_input.send_requested.connect(self.on_send)
        self.user_input.focus_changed.connect(self._ring_the_composer)
        self.user_input.textChanged.connect(self._fit_composer_to_text)
        inner.addWidget(self.user_input)
        self._fit_composer_to_text()

        strip = QtWidgets.QHBoxLayout()
        strip.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        strip.setSpacing(SPACE_SM)

        self.upload_widget = UploadWidget()
        self.upload_widget.file_attached.connect(self.chip_bar.add_file)
        self.chip_bar.file_removed.connect(self.upload_widget.discard)
        strip.addWidget(self.upload_widget)

        from .model_selector import EffortPopup, ModelSelectorPopup

        self.model_button = QtWidgets.QPushButton()
        self.model_button.setObjectName("PickerButton")
        self.model_button.setFixedHeight(CONTROL_DENSE)
        self.model_button.setSizePolicy(QtWidgets.QSizePolicy.Maximum,
                                        QtWidgets.QSizePolicy.Fixed)
        self.model_button.setToolTip("Active LLM model")
        self.model_button.setLayoutDirection(QtCore.Qt.RightToLeft)
        self._model_display = ""
        self.model_button.clicked.connect(self._open_model_selector)
        strip.addWidget(self.model_button)

        self.model_selector = ModelSelectorPopup(self)
        self.model_selector.model_chosen.connect(self._on_model_selected)

        self.effort_button = QtWidgets.QPushButton()
        self.effort_button.setObjectName("PickerButton")
        self.effort_button.setFixedHeight(CONTROL_DENSE)
        self.effort_button.setSizePolicy(QtWidgets.QSizePolicy.Maximum,
                                         QtWidgets.QSizePolicy.Fixed)
        self.effort_button.setToolTip("How hard the model is asked to think")
        self.effort_button.setLayoutDirection(QtCore.Qt.RightToLeft)
        self.effort_button.clicked.connect(self._open_effort_selector)
        strip.addWidget(self.effort_button)

        self.effort_selector = EffortPopup(self)
        self.effort_selector.effort_chosen.connect(self._on_effort_selected)
        self._show_effort()

        self._models = []
        self._current_model_id = None
        self._refresh_models(provider_manager.instance().get_available_models())
        provider_manager.instance().models_changed.connect(self._refresh_models)
        strip.addStretch()
        strip.addSpacing(SPACE_MD)

        self.send_button = QtWidgets.QPushButton()
        self.send_button.setObjectName("SendButton")
        self.send_button.setToolTip("Send message (Enter)")
        self.send_button.clicked.connect(self.on_send)
        strip.addWidget(self.send_button)

        self.stop_button = QtWidgets.QPushButton()
        self.stop_button.setObjectName("SendButton")
        self.stop_button.setIcon(UIHelpers.get_icon("square", size=ICON, on=self.stop_button))
        self.stop_button.setToolTip("Stop after the current attempt")
        self.stop_button.clicked.connect(self.on_stop)
        self.stop_button.setVisible(False)
        strip.addWidget(self.stop_button)

        inner.addLayout(strip)
        return card

    def _ring_the_composer(self, focused: bool):
        """
        The composer says which of the panel's fields the keyboard is in.

        A dynamic property and a repolish rather than a stylesheet of the
        card's own: a second `setStyleSheet` on the card would drop the
        panel-wide rule that gives it its fill, its radius and its margin,
        and Qt only re-reads a property-keyed selector when the style is
        asked to.
        """
        self._input_card.setProperty("focused", "true" if focused else "false")
        self._input_card.style().unpolish(self._input_card)
        self._input_card.style().polish(self._input_card)

    def _fit_composer_to_text(self):
        """
        The composer is one row when empty and grows with what is typed, up to
        ~40% of the panel, after which it scrolls.

        A percentage rather than a row count: the panel is narrow and dockable,
        so a height that is comfortable at one width wraps to nonsense at
        another. Frozen at three rows, as it was, anything past the third row
        was unreachable — it neither grew nor scrolled (ADR-0011).
        """
        document = self.user_input.document()
        document.setTextWidth(self.user_input.viewport().width())
        padding = 2 * SPACE_COMPOSER
        one_row = QtGui.QFontMetrics(self.user_input.font()).lineSpacing() + padding
        ceiling = max(one_row, int(self.height() * 0.4))
        wanted = int(document.size().height()) + padding

        self.user_input.setFixedHeight(min(max(wanted, one_row), ceiling))
        self.user_input.setVerticalScrollBarPolicy(
            QtCore.Qt.ScrollBarAsNeeded if wanted > ceiling
            else QtCore.Qt.ScrollBarAlwaysOff)

    def resizeEvent(self, event):
        """
        Refits the composer and the model pill, both of which are sized as a
        fraction of the panel and so move with it.

        Each is looked for rather than assumed: Qt resizes the dock while
        __init__ is still building it.
        """
        super().resizeEvent(event)
        if getattr(self, "user_input", None):
            self._fit_composer_to_text()
        if getattr(self, "_model_display", None):
            self._fit_the_model_pill()

    def showEvent(self, event):
        """
        Icons are tinted once the panel is on screen.

        A widget carries the global stylesheet's colour only after Qt has
        polished it; asked in __init__ it still answers with the OS palette,
        which is black under every FreeCAD dark theme (ADR-0011).
        """
        super().showEvent(event)
        for button, icon, size in ((self.new_btn, "plus", ICON),
                                   (self.history_btn, "history", ICON),
                                   (self.send_button, "send", ICON),
                                   (self.stop_button, "square", ICON),
                                   (self.model_button, "chevron_down", ICON_DENSE)):
            button.setIcon(UIHelpers.get_icon(icon, size=size, on=button))

    def _refresh_models(self, models=None):
        """
        Recompute which model the button shows (FCC-175).

        The stored id when the provider that owns it has confirmed it; the
        stored id held bare while its provider's fetch is still in flight,
        because showing anything else there is the flash of a model the user
        never picked (FCC-41); the fallback `_retire_model` names once every
        provider has answered and none offers it any more; or, with nothing
        stored at all, the first model on offer.
        """
        if models is None:
            models = provider_manager.instance().get_available_models()
        self._models = models
        last_id = settings_manager.instance().get_last_model()

        if not models:
            self._set_current_model(None, "No models — add API key")
            return

        entry = next((m for m in models if m["id"] == last_id), None) if last_id else None
        if entry is None and last_id:
            if provider_manager.instance().awaiting_model_lists():
                entry = {"id": last_id, "display": last_id, "provider": ""}
            else:
                fallback_id = self._retire_model(last_id)
                entry = next((m for m in models if m["id"] == fallback_id), None)
        if entry is None:
            entry = models[0]

        self._set_current_model(entry["id"], entry["display"])

    def _set_current_model(self, model_id, display: str):
        """
        The model pill, and the effort pill beside it: whether the choice of
        how hard to think means anything is a property of the model, so it is
        re-read every time the model changes.
        """
        self._current_model_id = model_id
        self._model_display = display
        self.model_button.setToolTip("Active LLM model — %s" % display)
        self._fit_the_model_pill()
        self._show_effort()

    def _fit_the_model_pill(self):
        """
        The model's name is elided to `PICKER_SHARE` of the panel's width, and
        the name in full moves to the tooltip.

        The pill sizes itself to its own text, and a provider's name for a
        model is not a name -- `claude-sonnet-4-5-20250929` and
        `gemini-2.5-flash-preview-05-20` are the ids the OpenAI-shaped routes
        hand back where a display name is missing. At that length the pill and
        the effort pill beside it are the whole strip, and Send is pushed to
        the edge of a docked panel; the attach button that sets the idiom for
        that row is 28 pixels wide. A share of the panel rather than a fixed
        ceiling because the panel is dockable and floats at any width, so a
        ceiling comfortable at one width is either wasteful or useless at the
        other.

        The share is of the whole pill, not of its text: the chevron beside
        the name and the stylesheet's padding are what the strip actually
        loses, so the button is asked what its own chrome costs rather than
        told, and the number left over is what the name is elided to.
        """
        metrics = QtGui.QFontMetrics(self.model_button.font())
        self.model_button.setText(self._model_display)
        chrome = (self.model_button.sizeHint().width()
                  - metrics.horizontalAdvance(self._model_display))
        room = max(CONTROL, int(self.width() * PICKER_SHARE) - chrome)
        self.model_button.setText(
            metrics.elidedText(self._model_display, QtCore.Qt.ElideRight, room))

    def _retire_model(self, model_id: str) -> str:
        """
        Every provider has answered and none offers ``model_id``. Say so.

        The fallback is the Preferences default, or the first model on offer,
        because that default is only a preference and may never have been set.
        """
        settings = settings_manager.instance()
        fallback = settings.get_default_model() or self._models[0]["id"]
        settings.set_last_model(fallback)
        self._queue_widget.now(
            "%s is no longer offered by its provider — switched to %s."
            % (model_id, fallback))
        return fallback

    def _open_model_selector(self):
        self.model_selector.open_at(self._models, self.model_button)

    def _on_model_selected(self, model_id: str):
        """Drops the client so the next send rebuilds it from settings."""
        settings_manager.instance().set_last_model(model_id)
        if getattr(self, "worker", None):
            self.worker.client = None
        entry = next((m for m in self._models if m["id"] == model_id), None)
        self._set_current_model(model_id, entry["display"] if entry else model_id)

    def _open_effort_selector(self):
        """
        Offers the levels the routes take, unless this model cannot reason at
        all: a picker that changes nothing is a picker that lies, so the
        trigger says so in its tooltip and does not open.
        """
        from core.llm import EFFORTS, effort_is_honoured

        if not effort_is_honoured(settings_manager.instance().get_last_model()):
            return
        self.effort_selector.open_at(list(EFFORTS), self.effort_button)

    def _on_effort_selected(self, level: str):
        """Drops the client so the next send is asked to think the new way."""
        settings_manager.instance().set_effort(level)
        if getattr(self, "worker", None):
            self.worker.client = None
        self._show_effort()

    def _show_effort(self):
        """
        The pill carries the level in force, and says when the model will
        ignore it.

        The level is read from `core.llm` and not from settings: what is
        stored may be empty, or a level from an older build, and the panel
        must show what the next run will actually be asked for.

        A model with no reasoning to spend cannot honour the choice, and the
        honest thing is to say so where the choice is made rather than to
        take it and drop it (`core.llm.effort_is_honoured`).
        """
        from core.llm import effort, effort_is_honoured

        honoured = effort_is_honoured(settings_manager.instance().get_last_model())
        self.effort_button.setText(effort())
        self.effort_button.setEnabled(honoured)
        self.effort_button.setToolTip(
            "How hard the model is asked to think" if honoured
            else "This model does no reasoning of its own")
        self.effort_button.setIcon(
            UIHelpers.get_icon("chevron_down", size=ICON_DENSE, on=self.effort_button))

    def toggle_history(self):
        """
        The picker is populated from the indexed Sessions only, each carrying
        its own position: a Session enters the picker when it gets its name, on
        its first Intent, and the live empty Session is on screen rather than in
        the history.

        It shows the Document in front of the user, not the archive: a chat
        belongs to the file it was about, and the twenty chats about the other
        twenty files are noise while this one is open. Everything is still
        reachable — a Document nothing has been said about, or one whose
        chats have all been pruned, falls back to the whole list rather than
        to an empty popup.
        """
        if self.history_popup.isVisible():
            self.history_popup.close()
        else:
            self.history_popup.populate(self._sessions_worth_showing())
            self.history_popup.drop_from_the_dock()

    def _sessions_worth_showing(self) -> list:
        """The indexed Sessions of the Document in focus, or all of them."""
        from core.freecad_tools import active_document

        try:
            identity = str(getattr(active_document(), "Uid", "") or "")
        except Exception:
            identity = ""
        rows = self.chat_sessions.indexed_sessions()
        if not identity:
            return rows
        here = [(position, info) for position, info in rows
                if info.get("document_id") == identity]
        return here or rows

    def new_chat(self):
        """
        Opens an empty Session. Its real name comes from its first Intent, so
        there is nothing to call it yet.
        """
        idx, _ = self.chat_sessions.add_new_chat("New Chat")
        self._became_the_live_session()
        self.update_header_title(idx)
        self._empty_state = self._build_empty_state()
        self.add_chat_widget(self._empty_state)

    def _reset_render_state(self):
        """
        What one run builds and the next must not draw into.

        There is room for one question at a time: the run is blocked on the
        answer, so it cannot have asked a second thing while the first is on
        screen.
        """
        self._plan_widget = None
        self._thought = None
        self._thought_phase = None
        self._current_step = 0
        self._question_card = None
        self._answers_to_enter = None
        self._total_usd = 0.0
        self._run_start_time = None
        self._last_event_time = None
        self._last_tool_call_time = None
        self._last_tool_call_widget = None
        self._last_tool_call_name = None
        self._update_usage_total()
        self._last_assertion_event = None
        self._last_review_verdict_event = None
        self._last_review_views = []

    def _update_usage_total(self):
        """
        Redraw the header's running total from `self._total_usd` and elapsed time.

        Time is computed from event.at deltas (run start to last event), so a
        replayed session shows the same duration the live run showed.
        """
        elapsed_seconds = 0.0
        if self._run_start_time is not None and self._last_event_time is not None:
            elapsed_seconds = self._last_event_time - self._run_start_time

        time_str = _format_elapsed(elapsed_seconds)
        self.usage_label.setText(f"{_format_cost(self._total_usd)}  {time_str}")

    def _became_the_live_session(self):
        """
        Let go of the render state of the Session being left.

        Owned Objects no longer need clearing here (FCC-181): each Session
        keeps its own `_SessionWorker`, so one Session's Owned Objects were
        never reachable from another's to begin with.

        The Queue is pinned above the composer rather than left behind in the
        transcript, so it is one of the things a Session lets go of: it stood
        down here, and whatever the Session being opened had running is put
        back by the replay that follows.
        """
        self._reset_render_state()
        self._queue_widget.stand_down()
        self._empty_state = None

    def rename_current_session(self):
        index = self.chat_sessions.stack.currentIndex()
        info = self.chat_sessions.get_session_info(index)
        if info is None:
            return
        name, chosen = QtWidgets.QInputDialog.getText(
            self, "Rename session", "Name:", QtWidgets.QLineEdit.Normal, info["name"])
        if chosen and name.strip():
            self.chat_sessions.rename_session(index, name)
            self.update_header_title(index)

    def switch_session(self, index: int):
        """
        Open a Session, resyncing what is shown to what actually happened.

        A run no longer holds the panel to the Session it started from
        (FCC-181): each Session runs on its own `_SessionWorker`, so switching
        away from one that is still going leaves it running rather than
        blocking or losing it. What was missed while away is on that Session's
        own transcript, and `restore_session` reads it back in — the same
        chunked replay a reopened historical Session already used, asked for
        again rather than only once, because a Session that kept running while
        unseen has more on its transcript than the last time this replayed it.
        """
        self.chat_sessions.switch_to(index)
        self._became_the_live_session()
        info = self.chat_sessions.get_session_info(index)
        bundle = self._workers.get(info["log"]) if info else None
        self.restore_session(index, resync=bool(bundle and bundle.dirty))
        if bundle is None:
            self._set_running(False)
            return
        bundle.dirty = False
        self._set_running(bundle.running)
        if bundle.pending_question is not None:
            kind, payload = bundle.pending_question
            self._dispatch_ask(kind, payload)

    def restore_session(self, index: int, resync: bool = False):
        """
        Replay the Session's log through the switch that rendered it live.

        `resync` bypasses "already restored" and re-reads the whole transcript
        regardless — for a Session whose worker kept running while it was not
        on screen, so what were stale widgets are rebuilt from what the
        transcript now actually holds (FCC-181). `clear_page` first, or the
        replay would land its widgets after whatever this Session showed the
        last time it was open, doubling everything that had already rendered.
        """
        from core.events import replay

        info = self.chat_sessions.get_session_info(index)
        if info is None:
            return
        due = self.chat_sessions.mark_restored(index)
        if not resync and not due:
            return
        if resync:
            self.chat_sessions.clear_page(index)

        events, truncated = replay(info["log"])
        if not events:
            self._empty_state = self._build_empty_state()
            self.add_chat_widget(self._empty_state)
            return

        self._replay_chunk(index, events, 0, truncated)

    def _replay_chunk(self, index: int, events: list, start: int, truncated: bool):
        """
        Replay a batch, then hand the event loop a turn before the next one.

        Replaying hundreds of Steps in one pass on the GUI thread froze the
        panel for as long as it took. The first batch is still synchronous, so
        a short Session is on screen by the time this returns.

        A Session the user has already switched away from is abandoned rather
        than rendered into whatever is on the stack now.
        """
        if self.chat_sessions.stack.currentIndex() != index:
            return

        for event in events[start:start + RESTORE_CHUNK]:
            self.on_event(event)

        done = start + RESTORE_CHUNK
        if done < len(events):
            QtCore.QTimer.singleShot(
                0, lambda: self._replay_chunk(index, events, done, truncated))
            return

        if truncated:
            self.add_chat_widget(AssistantMessageWidget(
                "This transcript ends mid-event — FreeCAD closed while the run was writing. "
                "Everything above it survived."))
        self._queue_widget.now("")

    @QtCore.Slot(str, str)
    def on_document_named(self, transcript: str, document: str, document_id: str):
        """
        Record which Document a Session worked on, so history can say so and
        so the panel can recognise that Document again.

        The name is what the picker shows; the id is the Document's own `Uid`,
        which is what it is recognised by — every unsaved Document in FreeCAD
        is called "Unnamed" (`slotActivateDocument`).
        """
        self.chat_sessions.name_document(transcript, document, document_id)

    def delete_session(self, index: int):
        info = self.chat_sessions.get_session_info(index)
        if info is None:
            return
        confirmed = QtWidgets.QMessageBox.question(
            self, "Delete session",
            "Delete “%s” and its whole transcript?" % info["name"],
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if confirmed != QtWidgets.QMessageBox.Yes:
            return
        self.chat_sessions.remove_session(index)
        bundle = self._workers.pop(info["log"], None)
        if bundle is not None:
            bundle.shut_down(self.SHUTDOWN_GRACE_MS)
        self.update_header_title(self.chat_sessions.stack.currentIndex())

    def _build_empty_state(self):
        """One muted line, so an empty panel still says where the user stands."""
        model = self.model_button.text().strip() or "no model"
        label = QtWidgets.QLabel(
            "%s. The Plan Gate asks before anything is written to your Document."
            % model)
        label.setObjectName("EmptyState")
        label.setWordWrap(True)
        label.setAlignment(QtCore.Qt.AlignCenter)
        return label

    @QtCore.Slot(int)
    def update_header_title(self, index: int):
        info = self.chat_sessions.get_session_info(index)
        if info:
            self.title_label.setText(info["name"])

    def _unusable_attachments(self) -> list:
        """
        Why the attached files would not reach the model, before the run starts.

        The chips stay on the bar, so the offending one can be taken off — or
        the model changed — and the same Intent sent again. A settings lookup
        that fails is not the user's problem here; the run reports it.
        """
        from core.llm import selected_model_name, unusable_attachments

        attached = self.chip_bar.files()
        if not attached:
            return []
        try:
            model = selected_model_name(self._current_model_id)
        except Exception:
            return []
        return unusable_attachments(attached, model)

    def on_send(self):
        """
        Sends the composer's text as an Intent.

        While the Plan is waiting on a question the composer answers, this
        answers it rather than starting a second run on the answer. The Session
        is named after the attachment check and never before it: an Intent that
        is turned away there never ran, and must not name the Session.
        """
        if self._answers_to_enter is not None:
            self._answer_with(self._answers_to_enter)
            return

        intent = self.user_input.toPlainText().strip()
        if not intent:
            return

        index = self.chat_sessions.stack.currentIndex()
        session = self.chat_sessions.get_session_info(index)
        if session is None:
            return

        unusable = self._unusable_attachments()
        if unusable:
            self.add_chat_widget(AssistantMessageWidget(
                "This would not have reached the model:\n\n"
                + "\n".join("• " + reason for reason in unusable)))
            return

        log = session["log"]
        bundle = self._worker_for(log)
        self._bind_document(bundle)

        self._name_session(index, log, intent)

        attachments = self.chip_bar.get_and_clear()
        bundle.sent_attachments = attachments

        if self._empty_state is not None:
            self._empty_state.setParent(None)
            self._empty_state = None
        self.user_input.clear()

        self._reset_render_state()

        bundle.running = True
        self._set_running(True)
        self._queue_widget.now("Planning…")
        outstanding = list(bundle.outstanding)
        self.request_work.emit(intent, attachments, log, outstanding)
        bundle.request_work.emit(intent, attachments, log, outstanding)

    def _name_session(self, index: int, log: str, intent: str):
        """
        The Session takes its name from this Intent, and enters the picker.

        The truncated Intent goes up immediately so nothing is ever called
        "Untitled"; the generated title replaces it when it arrives, and is
        asked for again on the next Intent if this one produced none (FCC-44).

        `begin` bounds the index and may have dropped Sessions before this one,
        so the position it had is not the position it has.
        """
        from core.session_title import fallback

        if self.chat_sessions.begin(index, fallback(intent)):
            index = self.chat_sessions.stack.currentIndex()
            self.update_header_title(index)
        if self.chat_sessions.wants_a_title(index):
            self._ask_for_a_title(log, intent)

    def _ask_for_a_title(self, log: str, intent: str):
        """
        Alongside the run, never in front of it.

        Qt owns the thread, so it is kept in a list: without a reference it is
        collected mid-flight.
        """
        thread = _TitleThread(intent, settings_manager.instance().get_naming_model(), self)
        thread.titled.connect(lambda title: self._title_arrived(log, title))
        self._title_threads.append(thread)
        thread.finished.connect(lambda: self._title_threads.remove(thread))
        thread.start()

    def _title_arrived(self, log: str, title: str):
        if self.chat_sessions.title_arrived(log, title):
            self.update_header_title(self.chat_sessions.stack.currentIndex())

    def on_stop(self):
        """
        Ends the run, and disables any question card with it: a card left
        clickable after Stop answers a run that is already over. Stopping the
        worker also releases a waiting question.
        """
        if self._question_card is not None:
            self._question_card.setEnabled(False)
            self._question_card = None
            self._answers_to_enter = None
            self._update_composer_enabled()
        bundle = self._workers.get(self._current_log())
        if bundle is not None:
            bundle.pending_question = None
        self.worker.stop()
        self.stop_button.setEnabled(False)
        self._queue_widget.now("Stopping after the current attempt…")

    def _set_running(self, running: bool):
        self._running = running
        self.send_button.setVisible(not running)
        self.stop_button.setVisible(running)
        self.stop_button.setEnabled(running)
        self._update_composer_enabled()

    def _update_composer_enabled(self):
        """
        Dark and unusable while the run holds the floor, back the moment it
        gives it up — either because it ended or because a Gate is the one
        asking, and a Gate's own answer (Amend, the values Plan was not given)
        is typed here. Greyed the way `AccordionWidget.mark_as_reasoning`
        subdues reasoning: an opacity effect, not a colour of its own
        (ADR-0011).
        """
        enabled = not self._running or self._question_card is not None
        self.user_input.setEnabled(enabled)
        if enabled:
            self.user_input.setGraphicsEffect(None)
        else:
            effect = QtWidgets.QGraphicsOpacityEffect(self.user_input)
            effect.setOpacity(0.6)
            self.user_input.setGraphicsEffect(effect)

    @QtCore.Slot(object)
    def on_event(self, event):
        """
        The whole panel is this method.

        What an event *says* is `core.narrate`'s, so the panel and the slice
        runner cannot word the same event differently. What is left here is
        where a line goes and what shape it takes: the Steps of a Plan, a block
        of code, a picture, reasoning as it streams, and the path of the
        document as a link that opens it.

        No calls back into the loop. A kind nobody narrates and nobody renders
        is simply not shown — nothing breaks.

        Structure is handled first: those events carry more than a sentence,
        and some of them also change the Plan widget's marks. The revised Steps
        are rendered afterwards, so they stand under the line that says they
        were revised.

        `INTENT` is the user's own turn, so it takes the user's bubble and not
        the narrated line: rendered here rather than at Send, a Session read
        back out of its transcript opens the same way the live one did, and
        the prompt is shown once instead of twice.
        """
        from core import events as ev
        from core.narrate import narrate

        data = event.data

        if event.kind == ev.INTENT:
            self._total_usd = 0.0
            self._run_start_time = event.at
            self._last_event_time = event.at
            self._update_usage_total()
            self._last_assertion_event = None
            self._last_review_verdict_event = None
            self._last_review_views = []
            self.add_chat_widget(TextMessageWidget(data.get("text", "")))
            self._update_queue(event)
            return

        self._update_queue(event)
        self._update_review_evidence(event)

        if event.kind == ev.CALL_STARTED:
            return

        if event.kind == ev.COST:
            self._total_usd += data.get("usd", 0.0)
            self._last_event_time = event.at
            self._update_usage_total()
            return

        if event.kind == ev.THOUGHT:
            self._render_thought(data["phase"], data["text"])
            return

        if event.kind == ev.ATTACHED:
            self._render_attachments(data["files"])
            return

        if event.kind == ev.TOOL_CALL:
            self._render_tool_call(data["name"], data["arguments"], event.at)
            return

        if event.kind == ev.TOOL_RESULT:
            self._time_the_tool_call(event.at)

        if event.kind == ev.PART:
            self._render_part(narrate(event).line, data["path"])
            return

        if event.kind == ev.PLAN:
            self._render_plan(data["steps"], "Plan", data.get("acceptance", ()),
                              data.get("unanswered", ()), data.get("decided", ()))
            return

        if event.kind == ev.ASSERTION and data.get("ok") is not None:
            self.add_chat_widget(AssertionResultsWidget(data))
            return

        if event.kind == ev.STEP_STARTED:
            self._thought = None
            self._current_step = data["index"]
            if self._plan_widget:
                self._plan_widget.highlight(data["index"])

        elif event.kind == ev.APPLIED and self._plan_widget:
            self._plan_widget.mark(data["step"], PLAN_DONE)

        elif event.kind == ev.BLOCKED and self._plan_widget and self._current_step:
            self._plan_widget.mark(self._current_step, PLAN_FAILED)

        if not self._card_will_repeat_it(event):
            self._say(narrate(event))

        if event.kind == ev.PLAN_REVISED:
            self._render_plan(data["steps"], "Revised Plan", data.get("acceptance", ()),
                              data.get("unanswered", ()), data.get("decided", ()))

    @staticmethod
    def _card_will_repeat_it(event) -> bool:
        """
        Whether the verdict card about to go up already carries this event's
        line, in which case the transcript does not need it twice.

        A Review that did not pass is always followed by a question -- the
        loop returns on `pass` and asks on everything else -- and the card
        that asks it is headed by the very sentence `narrate()` returns.
        Said again above it, the same words read as two verdicts.
        """
        from core import events as ev

        return (event.kind == ev.REVIEW_VERDICT
                and event.data.get("verdict") != "pass")

    def _update_queue(self, event):
        """
        The Queue's phase and Step marks and elapsed times.

        Time tracking uses event.at deltas so replayed sessions show the same
        durations the live run showed. CALL_STARTED starts the live call clock;
        COST ends it.
        """
        from core import events as ev
        from core.loop import APPROVE

        data = event.data

        if event.kind == ev.INTENT:
            self._queue_widget.reset(at=event.at)
            return

        if event.kind == ev.CALL_STARTED:
            phase = data.get("phase")
            ceiling = data.get("ceiling")
            self._queue_widget.call_started(phase, ceiling, event.at)
            return

        if event.kind == ev.COST:
            self._queue_widget.call_ended(event.at)
            return

        if event.kind == ev.PART:
            self._queue_widget.design_written(at=event.at)
        elif event.kind in (ev.PLAN, ev.PLAN_REVISED):
            self._queue_widget.start_plan(data["steps"], at=event.at)
        elif event.kind == ev.GATE and data.get("decision") == APPROVE:
            self._queue_widget.start_execute(data.get("steps"), at=event.at)
        elif event.kind == ev.STEP_STARTED:
            self._queue_widget.step_started(data["index"], at=event.at)
        elif event.kind == ev.APPLIED:
            self._queue_widget.step_applied(data["step"], at=event.at)
        elif event.kind == ev.UNBUILT:
            self._queue_widget.step_unbuilt(data["step"], at=event.at)
        elif event.kind == ev.PREFLIGHT_FAILED:
            self._queue_widget.step_failed(data["step"], at=event.at)
        elif event.kind == ev.ASSERTION:
            self._queue_widget.start_review(at=event.at)
        elif event.kind == ev.REVIEW_VERDICT:
            self._queue_widget.finish_review(at=event.at)
        elif event.kind in (ev.BLOCKED, ev.ERROR):
            self._queue_widget.fail_current(at=event.at)

    def _update_review_evidence(self, event):
        """
        What the Review verdict card shows, tracked off the stream as it
        passes (FCC-178) -- kept beside the narration rather than inside it,
        the same reason `_update_queue` is: a screenshot path and a raw
        Assertion/Review event are not words `narrate()` returns.

        Reset on `ASSERTION` rather than only at the top of a run: the
        acceptance checks are the one event guaranteed to land, in the same
        cycle, before `decided()` ever asks anything -- whether Review runs
        after them or the cycle stops there -- so a verdict left over from an
        earlier revision of this same run cannot be mistaken for the one
        belonging to this cycle.
        """
        from core import events as ev

        data = event.data

        if event.kind == ev.ASSERTION:
            self._last_assertion_event = event
            self._last_review_verdict_event = None
        elif event.kind == ev.TOOL_RESULT and data.get("name") == "review_views":
            self._last_review_views = list((data.get("result") or {}).get("views") or [])
        elif event.kind == ev.REVIEW_VERDICT:
            self._last_review_verdict_event = event

    def _say(self, narration):
        """
        Put a narrated event where it belongs: a line in the transcript, a
        collapsible row, or the Queue's line about what is happening now.

        A line past `FOLDS_OVER` folds into a row of its own. Design says what
        it could not settle in the drawing's own words, and one such paragraph
        in the run this was built against ran to 1,900 characters -- four
        screens of a docked panel, standing between the user and the Plan they
        opened the panel to read. Folded, its first sentence is the title and
        the rest is one click away.
        """
        if narration.line:
            self.add_chat_widget(_folded(narration.line)
                                 if len(narration.line) > FOLDS_OVER
                                 else AssistantMessageWidget(narration.line))

        if narration.title:
            row = AccordionWidget(narration.title, code=True)
            self.add_chat_widget(row)
            row.append_text(narration.body)

        if narration.status:
            self._queue_widget.now(narration.status)

    def _render_thought(self, phase, text):
        """
        Reasoning appends to one accordion per phase turn, as it forms.

        Only the chat scroll renders it: the Queue never carries a reasoning
        chunk, so a long chain of thought is read whole rather than clipped to
        whatever fit in ninety characters (FCC-173).

        A wordless chunk is the subscription route saying the model is
        reasoning and that the words are withheld (`core/llm.py`). The row
        goes up all the same, pulsing, because a phase that takes four minutes
        has to show it is alive -- and it does not open, since a row that
        opens onto nothing is worse than one that says it has nothing.
        """
        if self._thought is None or self._thought_phase != phase:
            self._thought = AccordionWidget("Thinking (%s)…" % phase)
            self._thought_phase = phase
            self._thought.mark_as_reasoning()
            self._thought.opens_onto_nothing()
            self.add_chat_widget(self._thought)
            self._thought.start_pulsing()
        if text:
            self._thought.append_text(text)

    def _render_attachments(self, files):
        """
        What the run was shown, in the chat beside the Intent (FCC-88).

        The picture itself, not a filename — the chips come off the bar on Send
        and nothing replaced them. A restored Session is the case that decides
        the shape: a clipboard paste is a temp file the panel deletes when the
        run ends, so the record has to outlive its file. Gone means the name,
        never a broken image and never a raised exception — `EventStream.emit`
        swallows a renderer that throws, so the failure would be silent.
        """
        for attached in files:
            name = attached.get("name") or ""
            picture = QtGui.QPixmap(attached.get("path") or "")
            if picture.isNull():
                self.add_chat_widget(_beside_the_intent(
                    FileChip(name or attached.get("path") or "", removable=False)))
                continue
            label = QtWidgets.QLabel()
            label.setPixmap(picture.scaledToWidth(
                min(ATTACHMENT_WIDTH, picture.width()),
                QtCore.Qt.SmoothTransformation))
            label.setToolTip(name)
            self.add_chat_widget(_beside_the_intent(label))

    def _render_part(self, line, path):
        """
        Above the Plan, on a card: the Gate is the Steps and the document they
        were built from, and a document is opened rather than poured into the
        chat (FCC-47 argued the other way over a description that had not been
        distilled yet, and a run's worth of one is read nowhere).

        The card carries the path the event carries, so what the user opens is
        the file the run is planned, checked and built from, and a correction
        made in it is a correction to the run (FCC-108). It is the `FileChip`
        the user's own attachments wear: a file is one shape in the design
        whichever side of the transcript wrote it, and an underlined path
        wrapped across three lines was the loudest thing in the chat. It stands
        at the left margin the run answers from rather than under the user's
        bubble, because the run is what wrote it.
        """
        if self._thought:
            self._thought.stop_pulsing()
            self._thought = None
        self.add_chat_widget(AssistantMessageWidget(
            line.replace(path, "").strip().rstrip(":").strip()))
        self.add_chat_widget(_at_the_left_margin(FileChip(path, removable=False)))

    def _render_plan(self, steps, title, acceptance=(), unanswered=(), decided=()):
        """
        The Plan's Steps, with what surrounds them at the Gate.

        Values the model settled for itself stand above the Steps: nothing is
        asked between the attachment and the Gate, so such a value is read
        here or it is not read at all, and it is read before the Steps built
        on it (ADR-0022). The questions nobody answered stand above the
        acceptance criteria, since those Steps carry values the Plan chose for
        itself and the Gate is the only place to overrule them. The criteria
        stand under the Steps for the same reason: the Gate is the only place
        a wrong criterion is correctable.

        Each of the three says in its heading how many it holds, and each
        stands open while it is short enough to be read where it stands.
        `OPEN_AT_MOST` is where that stops: the session this was measured
        against decided 22 values and set 40 acceptance criteria, each a
        sentence long, and opened those two alone are some four screens of
        dense text between the user and the Build button -- a Gate nobody
        scrolls to the bottom of is not read either. Past that the row folds
        and the count carries it: "40 criteria" is the part that has to be
        seen, and the click is for the reader who wants the forty.

        Few is not the same as short: the four values that run settled for
        itself are four paragraphs of a conflict it could not resolve, 1,900
        characters between the user and the Plan. A list stands open while it
        is both short in entries and short in words, `FOLDS_OVER` being the
        second budget.
        """
        if self._thought:
            self._thought.stop_pulsing()
            self._thought = None
        def beside_the_steps(heading, entries):
            if not entries:
                return
            row = AccordionWidget("%s (%d)" % (heading, len(entries)))
            self.add_chat_widget(row)
            listed = "\n".join("• " + str(entry) for entry in entries)
            row.append_text(listed)
            if len(entries) <= OPEN_AT_MOST and len(listed) <= FOLDS_OVER:
                row.toggle_content()

        beside_the_steps("Decided for you", decided)
        self._plan_widget = PlanWidget(steps, title=title)
        self.add_chat_widget(self._plan_widget)
        beside_the_steps("Nobody answered these — the Plan chose", unanswered)
        beside_the_steps("What it has to satisfy", acceptance)

    def _render_tool_call(self, name, arguments, event_time):
        """
        A call takes one collapsible shape in the stream, not three (ADR-0011),
        and it says which Step it belongs to.

        The code of a Step is the longest thing the transcript ever carries and
        the least often read: shown open, four Steps of it are the whole panel,
        and the run reads as though nothing is happening between them. Named
        and closed, the line that scrolls past says what is running -- which is
        what a user watching a long run is actually looking for -- and the code
        is one click away. Every call the loop makes to build geometry carries
        `code`; before this, those were the only ones with no name on them at
        all, so a run's visible history was the two calls that read the
        drawing.

        Duration is added when TOOL_RESULT arrives, from event.at deltas.
        """
        if self._thought:
            self._thought.stop_pulsing()
            self._thought = None
        title = ("Step %d — %s" % (self._current_step, name) if self._current_step
                 else name)
        row = AccordionWidget(title, code=True, boxed=True)
        self.add_chat_widget(row)
        row.append_text(arguments["code"] if "code" in arguments else str(arguments))

        self._last_tool_call_time = event_time
        self._last_tool_call_widget = row
        self._last_tool_call_name = name

    def _time_the_tool_call(self, at):
        """
        How long the call that just answered took, put on its own closed row.

        On the row rather than in the transcript as a line of its own: a run
        takes nine of these and a line each would be nine lines saying a
        number. The row is already there and already says which call it was,
        and the seconds are what tell a look that cost a provider turn from
        one that cost a millisecond.

        `TOOL_RESULT` still narrates after this -- the loop's calls are
        sequential, so the row being timed is always the last one opened.
        """
        if not (self._last_tool_call_widget and self._last_tool_call_time):
            return
        named = ("Step %d — %s" % (self._current_step, self._last_tool_call_name)
                 if self._current_step else self._last_tool_call_name)
        self._last_tool_call_widget.toggle_button.setText(
            "%s  (%.1fs)" % (named, at - self._last_tool_call_time))
        self._last_tool_call_widget = None
        self._last_tool_call_time = None
        self._last_tool_call_name = None

    @QtCore.Slot(object)
    def on_plan_gate(self, steps):
        """
        The Plan Gate. One approval covers every Step; there is no per-Step
        approval, so this appears once per Plan and the run waits on it. A
        revised Plan is a new Plan and gets its own card (FCC-68).

        One answer now, not four (FCC-177): the Plan is edited directly on
        `self._plan_widget` -- a Step's text, an add, a delete, a drag to
        reorder -- and Build reads whatever it holds at the moment it is
        pressed, edited or as-generated, and locks the widget in the same
        breath so nothing typed after Build changes what already ran.
        """
        from core.loop import APPROVE, Approval

        plan_widget = self._plan_widget

        def build():
            edited = list(plan_widget.steps()) if plan_widget else list(steps)
            if plan_widget:
                plan_widget.lock()
            return Approval(APPROVE, steps=tuple(edited))

        self._put_the_question(
            "Waiting for you to approve the Plan\u2026",
            "Run this Plan? Approving covers all %d Steps \u2014 no Step is confirmed "
            "separately." % len(steps),
            "Click a Step to edit it, or add and remove Steps above, then Build. You are "
            "still asked before anything of yours is deleted, before the model is "
            "rebuilt, and about a result that did not pass.",
            (("Build", build),))

    @QtCore.Slot(object)
    def on_revision(self, failure):
        """
        What to do about a result that did not pass, before a revision may wipe
        it. Three answers, and the Gate's three are not them: REJECT stops a
        run and does not licence a restart, so "start over" needs a word of its
        own (FCC-77).

        Whether Review ever ran is what tells the two askers behind this one
        signal apart (FCC-178): the acceptance checks can fail and stop the
        run before Review is asked anything, and `_last_review_verdict_event`
        is `None` for exactly that case, since `_update_review_evidence`
        clears it at the acceptance checks' own event. Review having run puts
        up the Confirmation card the epic names -- Approve, Reject, Start
        Over, with what Review looked at above them; without one this stays
        the plain question, since there is no Review result yet to show, and
        the reason is said above it in a line of its own.

        Said above the card as well, that line is the third place the same
        sentence appears in one screen: the card is headed by it, and Review
        emitted it before either.
        """
        from core.loop import ACCEPT, REVISE, START_OVER

        applied = failure.get("applied", 0)
        if self._last_review_verdict_event is not None:
            self._put_the_review_verdict(ACCEPT, REVISE, START_OVER)
            return

        self.add_chat_widget(AssistantMessageWidget(
            "The result did not pass, after %d Step%s were applied:\n%s"
            % (applied, "" if applied == 1 else "s", failure.get("why", ""))))

        self._put_the_question(
            "Waiting for you to decide what to do\u2026",
            "What should happen to what has been built?",
            "Take it as it is ends the run and keeps the model. Fix it keeps the model "
            "and plans the rest. Start over lets the next Plan delete it and build again.",
            (("Take it as it is", lambda: ACCEPT),
             ("Fix it", lambda: REVISE),
             ("Start over", lambda: START_OVER)))

    def _put_the_review_verdict(self, accept, revise, start_over):
        """
        The Confirmation card FCC-178 asks for: `ReviewVerdictWidget`, fed
        from what `_update_review_evidence` already read off the stream, its
        `decided` signal answering the same waiting worker thread every other
        card here answers through `_answer_with`.

        `assertion_line` is `summary_line`'s "N passed, M failed" (FCC-179)
        where there is a pass/fail count to give -- the plain narration line
        stays the fallback for the one case that is not a count at all, the
        checks that could not be written.
        """
        from core.narrate import narrate

        self._queue_widget.now("Waiting for you to review the result\u2026")

        assertion_data = self._last_assertion_event.data if self._last_assertion_event else {}
        card = ReviewVerdictWidget(
            view_paths=self._last_review_views,
            assertion_line=(summary_line(assertion_data)
                            or (narrate(self._last_assertion_event).line
                                if self._last_assertion_event else "")),
            verdict_line=narrate(self._last_review_verdict_event).line)
        answers = {"approve": lambda: accept, "reject": lambda: revise,
                  "start_over": lambda: start_over}
        card.decided.connect(lambda choice: self._answer_with(answers[choice]))

        self._question_card = card
        self._answers_to_enter = None
        self._update_composer_enabled()
        self.add_chat_widget(card)

    @QtCore.Slot(object)
    def on_missing_values(self, question):
        """
        The values Plan has not been given (FCC-25).

        The question itself is already in the transcript \u2014 it arrived as an
        event. This is only the card that takes the answer, and it takes it
        from the composer, the same way Amend does. Enter is the only route to
        Send here, the button being hidden mid-run.
        """
        def send():
            return self._from_composer("Give the values, then press Send\u2026",
                                       lambda said: said)

        self._put_the_question(
            "Waiting for the values the Plan needs\u2026",
            "",
            "Answer in the box below, then Send. Or let the Copilot choose \u2014 it will "
            "write each assumed value into the Plan, so you can still correct it.",
            (("Send", send),
             ("You decide", lambda: "")),
            enter=send)

    @QtCore.Slot(object)
    def on_owned_objects(self, kinds):
        """
        The Owned Object rule. Deletion only \u2014 a modification is covered by the
        Plan Gate, which approved a Step that said the object changes.

        The same card as every other question rather than a modal box: this one
        used to be the exception, and being the exception is how it ended up
        outside the transcript as well (FCC-88). What is said about it, before
        and after, is on the stream from `ApplyPipeline`.
        """
        deletes = list(kinds.get("delete") or ())

        self._put_the_question(
            "Waiting for you to allow a deletion\u2026",
            "Delete %s?" % ", ".join(deletes),
            "%s did not create %s in this session, so this is your own work. Ctrl+Z will "
            "still undo it as one Step."
            % ("The assistant", "these" if len(deletes) > 1 else "it"),
            (("Allow", lambda: True),
             ("Refuse", lambda: False)))

    def _put_the_question(self, waiting_for, prompt, note, choices, enter=None):
        """
        Ask the user the one way this panel asks anything.

        Four questions reach here \u2014 the Plan Gate, what to do about a result
        that did not pass, the values Plan was not given, and permission to
        delete the user\u2019s own object. They were four bespoke cards, three of
        them the same forty lines with different words and the fourth a modal
        box that answered outside the transcript entirely.

        A `choice` is a label and a callable returning the answer to hand back
        to the worker thread, which is blocked waiting for it. Returning None
        leaves the card up \u2014 that is how a composer with nothing in it nags
        instead of answering.

        `enter` is the choice the composer's Enter key takes, where there is
        one. Only the question that is answered by typing has it: Enter at the
        Plan Gate would amend a Plan the user was reading, not approving.

        The first choice is the one the card is asking for, so it is drawn as
        the panel's one filled button and laid out last: the buttons are right
        aligned and read outwards from the corner the eye ends at, which is
        where a confirmation puts what it wants pressed.

        The question is asked without a card of its own -- the design system's
        own Confirmation is plain rows and two buttons. What marks it out is
        the filled button, which is the only one on screen while it is up.
        """
        self._queue_widget.now(waiting_for)

        card = QtWidgets.QFrame()
        card.setObjectName("GateCard")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_SM)

        if prompt:
            asked = QtWidgets.QLabel(prompt)
            asked.setWordWrap(True)
            asked.setStyleSheet(css(LABEL))
            layout.addWidget(asked)

        explained = QtWidgets.QLabel(note)
        explained.setWordWrap(True)
        explained.setStyleSheet(css(CAPTION))
        layout.addWidget(explained)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(SPACE_SM)
        buttons.addStretch()
        for position, (label, decide) in reversed(list(enumerate(choices))):
            button = QtWidgets.QPushButton(label)
            button.setObjectName("PrimaryButton" if position == 0 else "PillButton")
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, d=decide: self._answer_with(d))
            buttons.addWidget(button)
        layout.addLayout(buttons)

        self._question_card = card
        self._answers_to_enter = enter
        self._update_composer_enabled()
        self.add_chat_widget(card)

    def _answer_with(self, decide):
        """
        Close the card on an answer; leave it up when there is not one yet.

        An answered card is spent, and says so in the two ways this panel has
        of saying it. The accent leaves: nothing is being asked any more, and
        the stylesheet's own rule is that exactly one control on screen wears
        the accent fill, so the button that wore it is demoted to the outline
        every secondary answer beside it already had. And the card is disabled
        and subdued -- an opacity effect, not a colour of its own (ADR-0011,
        `AccordionWidget.mark_as_reasoning`).

        Disabling alone changed nothing anyone could see, which is why the
        demotion is here at all: `background-color` is written by the
        stylesheet, and a stylesheet rule outranks the disabled palette Qt
        would otherwise grey the button with. The demotion is a change of
        `objectName` and a repolish rather than a stylesheet of the button's
        own, because a rule set on the widget would drop the panel's, and
        `PillButton` is already the shape wanted.
        """
        answer = decide()
        if answer is None:
            return

        card, self._question_card = self._question_card, None
        self._answers_to_enter = None
        self._update_composer_enabled()
        if card is not None:
            for button in card.findChildren(QtWidgets.QPushButton):
                if button.objectName() in ("PrimaryButton", "ReviewApprove"):
                    button.setObjectName("PillButton")
                    button.style().unpolish(button)
                    button.style().polish(button)
            card.setEnabled(False)
            spent = QtWidgets.QGraphicsOpacityEffect(card)
            spent.setOpacity(0.45)
            card.setGraphicsEffect(spent)
        self._queue_widget.now("Working\u2026")
        bundle = self._workers.get(self._current_log())
        if bundle is not None:
            bundle.pending_question = None
        self.ask.answer(answer)

    def _from_composer(self, nag, into):
        """
        Take the answer from the composer, echo it, and shape it.

        None when there is nothing typed: the placeholder says what is wanted
        and the card stays up, because an empty answer here is a slip rather
        than a decision \u2014 the questions where "nothing" is a real answer
        offer it as a button of its own.
        """
        said = self.user_input.toPlainText().strip()
        if not said:
            self.user_input.setPlaceholderText(nag)
            self.user_input.setFocus()
            return None

        self.user_input.clear()
        self.add_chat_widget(TextMessageWidget(said))
        return into(said)

    def on_run_finished(self, result, log: str):
        """
        Ends the run and carries its unmet acceptance checks forward, whatever
        the run ended as, including a result the user took as it is: the entries
        are wrong in the Document either way, and the next Intent inherits them.
        A run that passed clears the list.

        *log* names which Session finished — this Session's own worker keeps
        running whether or not it is the one on screen (FCC-181), so the
        bookkeeping happens here regardless, and only the widgets are guarded
        behind "is this the Session being looked at right now".
        """
        bundle = self._workers.get(log)
        if bundle is None:
            return
        bundle.running = False
        self.upload_widget.discard(*bundle.sent_attachments)
        bundle.sent_attachments = []
        bundle.outstanding = list(getattr(result, "unmet", None) or ())
        if log != self._current_log():
            bundle.dirty = True
            return

        self._set_running(False)
        if self._thought:
            self._thought.stop_pulsing()
            self._thought = None
        if result is not None and result.ok:
            self._queue_widget.now("")
        else:
            self._queue_widget.now("Stopped. Your Document is unchanged beyond the "
                                   "Steps already applied.")

    def add_chat_widget(self, widget: QtWidgets.QWidget):
        """
        Adds a widget to the transcript, before the trailing stretch, which is
        what keeps a short transcript from being spread down the whole panel.
        Following the bottom is the scroll area's own business, and only when
        the user has not scrolled back to read.
        """
        chat_history = self.chat_sessions.get_current_chat_history()
        if not chat_history:
            return None
            
        container = chat_history.widget()
        if container and container.layout():
            layout = container.layout()
            layout.insertWidget(max(layout.count() - 1, 0), widget)

        return widget

    SHUTDOWN_GRACE_MS = 3000

    def _cleanup_thread(self):
        """
        Stop every Session's worker, whatever each is in the middle of.

        `quit()` only asks a thread's event loop to end, and a run is not
        waiting for events — it is inside a call: parked on a question, or on
        a request in flight. Waiting on that without first releasing the
        question is FreeCAD hanging on exit, with no window left to answer
        from. The headless FreeCAD behind each Session's own Preflight Worker
        is this panel's child process and goes with it.

        Every worker is told to stop before any thread is waited on
        (FCC-181): a Session parked on a question blocks its own thread until
        released, and waiting on one before releasing the next would serialise
        what should be a handful of independent, already-blocked threads.

        `SHUTDOWN_GRACE_MS` is what a run in flight is given to notice it was
        stopped before the thread is ended from outside, long enough for a
        request already sent to unwind.
        """
        FreeCAD.removeDocumentObserver(self)
        for bundle in self._workers.values():
            bundle.worker.stop()
            if bundle.worker.pipeline is not None:
                bundle.worker.pipeline.shutdown()
        for bundle in self._workers.values():
            if bundle.thread.isRunning():
                bundle.thread.quit()
                if not bundle.thread.wait(self.SHUTDOWN_GRACE_MS):
                    bundle.thread.terminate()
                    bundle.thread.wait(1000)

    def slotDeletedDocument(self, document):
        """
        A Document closing ends whichever Session was bound to it (FCC-181).

        FreeCAD calls every registered observer's `slotDeletedDocument` by
        this exact name — no base class to inherit, just the method present.
        The Session itself, its transcript and its place in the picker are
        untouched; only the worker is torn down, the same as `delete_session`
        tears one down, so nothing keeps running against a Document that is
        no longer there to run against.
        """
        for log, bundle in list(self._workers.items()):
            if bundle.worker.document is document:
                bundle.shut_down(self.SHUTDOWN_GRACE_MS)
                del self._workers[log]

    def slotActivateDocument(self, document):
        """
        The MDI tab the user brought forward in FreeCAD moves the panel too.

        The Document's `Uid` is looked up against every Session's recorded
        `"document_id"`, last one first — a Document worked on by more than
        one Session (a second chat opened against the same file) resolves to
        whichever of them ran most recently, since Sessions are only ever
        appended and this index runs newest-last. A Document no Session has
        worked on — nothing has been sent to it — leaves the panel where it
        was; there is nothing to switch to.

        The `Uid` and not `Name`: FreeCAD calls every unsaved Document
        "Unnamed", so matching on the name made a brand-new empty Document
        open the chat of some older unsaved one, which is the panel losing
        the user's place for no reason they can see.
        """
        identity = str(getattr(document, "Uid", "") or "")
        if not identity:
            return
        for index in range(self.chat_sessions.count() - 1, -1, -1):
            info = self.chat_sessions.get_session_info(index)
            if info and info.get("document_id") == identity:
                if index != self.chat_sessions.stack.currentIndex():
                    self.switch_session(index)
                return

    def closeEvent(self, event):
        self.upload_widget.discard_temporary()
        self._cleanup_thread()
        super().closeEvent(event)
