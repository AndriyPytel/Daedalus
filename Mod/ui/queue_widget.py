"""
The Queue renders the run's five macro-phases, Execute's foreseen Steps
nested beneath it, purely as a renderer in `PlanWidget`'s shape: told what
happened and never asking the loop anything, with the five phase keys
reached through `sdk` rather than `core` directly. No
`phase_started`/`phase_ended` event reaches the wire, so every transition
here is read off a structural event that already carries the fact: `PART`
says Design finished and Plan started, `PLAN`/`PLAN_REVISED` closes a
Design that wrote no part file, an approved `GATE` says Plan finished, a
Step's own terminal event at the last index says Execute finished,
`ASSERTION` says Assertion finished, and `REVIEW_VERDICT` says Review
finished, each doubling as the next phase's Running mark since nothing else
announces one starting. Preflight is not a sixth row, since it runs per-
Step inside Execute alongside Apply.
"""
try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from ui.design_tokens import (FAIL, ICON, ICON_DENSE, RUNNING as RUNNING_COLOUR,
                              SPACE_MD, SPACE_SM, SPACE_XS)
from ui.typography import BODY, BODY_STRONG, CAPTION, css
from ui.ui_helpers import UIHelpers
from sdk import (ASSERTION_PHASE as ASSERTION, DESIGN_PHASE as DESIGN,
                         EXECUTE_PHASE as EXECUTE, PLAN_PHASE as PLAN,
                         REVIEW_PHASE as REVIEW)

PHASES = (DESIGN, PLAN, EXECUTE, ASSERTION, REVIEW)

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

_ICON = {QUEUED: "circle", RUNNING: "circle_dot", DONE: "circle_check", FAILED: "circle_x"}

_LABEL = {DESIGN: "Design", PLAN: "Plan", EXECUTE: "Execute",
          ASSERTION: "Assertion", REVIEW: "Review"}

_MARK_COLOUR = {RUNNING: RUNNING_COLOUR, FAILED: FAIL}


class _NowLabel(QtWidgets.QLabel):
    """
    One line, elided, whatever it is handed.

    Pinned above the composer, so it cannot wrap: the sentences that reach it
    run to ninety characters and more, and three of those lines are the
    transcript's bottom third on a docked panel.
    """

    def __init__(self):
        super().__init__("")
        self._full = ""
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)

    def set_full(self, text):
        self._full = text or ""
        self.setToolTip(self._full)
        self.setVisible(bool(self._full))
        self._relayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self):
        metrics = QtGui.QFontMetrics(self.font())
        super().setText(metrics.elidedText(self._full, QtCore.Qt.ElideRight,
                                           max(0, self.width())))


class _StepLabel(QtWidgets.QLabel):
    """A Step description on one line, opened to the whole of it by a click.

    A Step arrives as a paragraph written for the model (this run foresaw
    fourteen Steps averaging 480 characters). Collapsed is one elided
    line, expanded the paragraph; the click that swaps them is the row.
    """

    def __init__(self, text: str):
        super().__init__(text)
        self._full = text
        self._open = False
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(text)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)

    def mousePressEvent(self, event):
        self._open = not self._open
        self.setWordWrap(self._open)
        self.updateGeometry()
        self._relayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self):
        if self._open:
            super().setText(self._full)
            return
        metrics = QtGui.QFontMetrics(self.font())
        super().setText(metrics.elidedText(self._full, QtCore.Qt.ElideRight,
                                           max(0, self.width())))


def _row(layout, text, size, step=False):
    """One icon + text row, added to *layout*, paired with its painted state.

    The icon pins to the first line, not centred: an opened Step spans
    several lines and a centred mark stops reading as its state. A
    QFrame, not a plain widget, lets the stylesheet wash the row on hover.
    """
    container = QtWidgets.QFrame()
    container.setObjectName("QueueRow")
    row = QtWidgets.QHBoxLayout(container)
    row.setContentsMargins(SPACE_SM, SPACE_XS, SPACE_SM, SPACE_XS)
    row.setSpacing(SPACE_SM)
    icon = QtWidgets.QLabel()
    icon.setFixedWidth(size)
    icon.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignHCenter)
    label = _StepLabel(text) if step else QtWidgets.QLabel(text)
    row.addWidget(icon, 0, QtCore.Qt.AlignTop)
    row.addWidget(label, 1)
    layout.addWidget(container)
    return container, icon, label


def _paint(icon_label, text_label, state, size):
    """The state of one row is put on its mark and on the weight of its text, not
on the text's colour: this design language swaps the glyph beside it rather
than repainting it, so a failed row gets a cross, not a red row. The two
semantic colours go on the glyph, not the text.
"""
    icon_label.setPixmap(
        UIHelpers.get_icon(_ICON[state], size=size, on=text_label,
                           colour=_MARK_COLOUR.get(state)).pixmap(size, size))
    text_label.setStyleSheet(css(BODY_STRONG if state == RUNNING else BODY))


def _format_time(seconds):
    """
    Wall-clock duration for Queue display, zero-padded M:SS or H:MM:SS.

    90 seconds -> "1:30"
    3660 seconds -> "1:01:00"
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


class QueueWidget(QtWidgets.QFrame):
    """Where the run is, pinned above the composer: one row per phase, Execute's
foreseen Steps nested under its row. Folded, two lines show the phase
running and what is happening inside it, the Step being applied or that the
run waits on the user; unfolded, the five phases and every foreseen Step
show, more than fits above a composer, so it is capped and scrolls. A phase
already `FAILED` never moves again, since every method below leaves it as
is.
"""

    CEILING = 0.4

    def __init__(self):
        super().__init__()
        self.setObjectName("QueueCard")

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        outer.setSpacing(0)

        self._states = {phase: QUEUED for phase in PHASES}
        self._states[DESIGN] = RUNNING
        self._icon_labels = {}
        self._text_labels = {}
        self._phase_start_times = {}
        self._phase_end_times = {}
        self._phase_totals = {}
        self._step_start_times = {}
        self._step_end_times = {}
        self._current_call_start = None
        self._current_call_ceiling = None
        self._current_call_phase = None
        self._latest_event_time = None
        self._foreseen = []
        self._step_states = []
        self._step_rows = []
        self._step_icons = []
        self._step_labels = []
        self._open = False
        self._live = False
        self._spin_angle = 0
        self._spin_base = None
        self._spin_timer = QtCore.QTimer(self)
        self._spin_timer.setInterval(100)
        self._spin_timer.timeout.connect(self._spin_tick)
        self.setVisible(False)

        self._head_row, self._head_icon, self._head_label = _row(outer, "", ICON)
        self._head_row.setCursor(QtCore.Qt.PointingHandCursor)
        self._head_row.mousePressEvent = lambda event: self.toggle()
        self._chevron = QtWidgets.QLabel()
        self._head_row.layout().addWidget(self._chevron, 0, QtCore.Qt.AlignTop)

        self._now_label = _NowLabel()
        self._now_label.setStyleSheet(css(CAPTION))
        outer.addWidget(self._now_label)

        self._all = QtWidgets.QScrollArea()
        self._all.setWidgetResizable(True)
        self._all.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._all.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._all.setVisible(False)
        phases = QtWidgets.QWidget()
        listed = QtWidgets.QVBoxLayout(phases)
        listed.setContentsMargins(0, SPACE_SM, 0, 0)
        listed.setSpacing(0)
        self._all.setWidget(phases)
        outer.addWidget(self._all)

        for phase in PHASES:
            _, icon, label = _row(listed, _LABEL[phase], ICON)
            self._icon_labels[phase] = icon
            self._text_labels[phase] = label
            if phase == EXECUTE:
                self._steps_container = QtWidgets.QWidget()
                self._steps_area = QtWidgets.QVBoxLayout(self._steps_container)
                self._steps_area.setContentsMargins(SPACE_MD, 0, 0, 0)
                self._steps_area.setSpacing(0)
                self._steps_container.setVisible(False)
                listed.addWidget(self._steps_container)
        listed.addStretch(1)

        self._repaint_phases()

    def reset(self, at=None):
        """
        Back to the state a fresh run starts in, this widget being pinned
        rather than one more thing in the transcript: a new Intent replaces
        what it says instead of adding a second Queue below the first.

        `stand_down` is the opposite call: there, no run belongs to this
        chat at all, so nothing is left running or visible for `reset` to
        replace.
        """
        for row in self._step_rows:
            row.setParent(None)
        self._step_rows = []
        self._step_icons = []
        self._step_labels = []
        self._step_states = []
        self._foreseen = []
        self._states = {phase: QUEUED for phase in PHASES}
        self._states[DESIGN] = RUNNING
        self._phase_start_times = {}
        self._phase_end_times = {}
        self._phase_totals = {}
        self._step_start_times = {}
        self._step_end_times = {}
        self._current_call_start = None
        self._current_call_ceiling = None
        self._current_call_phase = None
        self._latest_event_time = None
        self._steps_container.setVisible(False)
        self._live = True
        self.setVisible(True)
        self._now_label.set_full("")

        if at is not None:
            self._phase_start_times[DESIGN] = at
            self._latest_event_time = at

        self._repaint_phases()

    def stand_down(self):
        """
        No run belongs to this chat at all -- a new chat, or one switched to
        that has never run: unlike `reset`, nothing is left running and the
        Queue is hidden rather than pinned open.

        Fixes the panel opening on a chat with no run of its own still
        claiming, from the last chat's state, that a phase is running.
        """
        for row in self._step_rows:
            row.setParent(None)
        self._step_rows = []
        self._step_icons = []
        self._step_labels = []
        self._step_states = []
        self._foreseen = []
        self._states = {phase: QUEUED for phase in PHASES}
        self._steps_container.setVisible(False)
        self._now_label.set_full("")
        self._live = False
        self._open = False
        self._all.setVisible(False)
        self._spin_timer.stop()
        self.setVisible(False)
        self._repaint_phases()

    def now(self, text):
        """What is happening inside the phase that is running, in one line.

        Every sentence pinned under the transcript arrives here: the Step
        applied, the question the run is blocked on, the model it switched
        to, and how it stopped. One line, elided, since a wrapped paragraph
        here would push the transcript off the panel; it shows before any
        run has started too -- a provider retiring the model is said to a
        panel asked to do nothing yet.
        """
        self._now_label.set_full(text)
        self.setVisible(self._live or bool(text))

    def toggle(self):
        self._open = not self._open
        self._all.setVisible(self._open)
        if self._open:
            self._fit_open_height()
        self._paint_head()

    def _timed(self, at, closing=None, opening=None):
        """One phase's clock, stamped by the same call that moves its mark.
        Every verb below changes state and, given `at`, records when that
        happened, so a split pair cannot print a duration for a phase left
        behind. RUNNING adds its span to `_phase_totals`, so a reopened
        phase keeps its earlier time.
        """
        if at is None:
            return
        if closing is not None and self._states[closing] == RUNNING:
            self._phase_end_times[closing] = at
            self._accumulate(closing, at)
        if opening is not None and self._states[opening] != RUNNING:
            self._phase_start_times[opening] = at
        self._latest_event_time = at

    def _accumulate(self, phase, at):
        start = self._phase_start_times.get(phase)
        if start is not None:
            self._phase_totals[phase] = self._phase_totals.get(phase, 0) + (at - start)

    def design_written(self, at=None):
        """Design ended: it wrote the part down, and Plan is already running.

        `PART` is the one event that says Design is over -- `_written_down`
        emits it at the end of `_describe_the_part` and nothing else does --
        while `DESIGN` fires mid-phase with five rounds still to run. Plan
        starts here rather than waited for: the loop goes straight into
        `replan()` with only a snapshot between, and a Plan taking
        thirty-one minutes to answer would otherwise leave no row Running.
        """
        self._timed(at, DESIGN, PLAN)
        if self._states[DESIGN] != FAILED:
            self._states[DESIGN] = DONE
        if self._states[PLAN] != FAILED:
            self._states[PLAN] = RUNNING
        self._repaint_phases()

    def start_plan(self, steps, at=None):
        """
        Plan answered -- the first Plan, or a revision of one.

        Design is closed here only as a fallback: a run whose Design wrote no
        part file emits no `PART`, and a Design left Running under a Running
        Plan is a state no run is ever in. A replan can land while Execute,
        Assertion or Review is still Running from the pass this one
        supersedes, so each is closed here too, adding its span to
        `_phase_totals` before it is put back to Queued.
        """
        self._timed(at, DESIGN, PLAN)
        if self._states[DESIGN] != FAILED:
            self._states[DESIGN] = DONE
        if self._states[PLAN] != FAILED:
            self._states[PLAN] = RUNNING
        for phase in (EXECUTE, ASSERTION, REVIEW):
            if self._states[phase] == RUNNING and at is not None:
                self._accumulate(phase, at)
            if self._states[phase] != FAILED:
                self._states[phase] = QUEUED
        self._foreseen = list(steps)
        self._steps_container.setVisible(False)
        self._repaint_phases()

    def waited(self, seconds):
        """
        The user's own time, taken off the clock of the phase that was running.

        A run sits at the Plan Gate until Build is clicked, and that wait is
        not work the row should charge for: Plan read 31 minutes where the
        model had answered in one. The running phase's start is moved forward
        rather than each row keeping an idle tally of its own, because every
        clock here is the difference between two `event.at` stamps and
        nothing else.
        """
        running = next((phase for phase in PHASES if self._states[phase] == RUNNING), None)
        if running is not None and running in self._phase_start_times:
            self._phase_start_times[running] += seconds
            self._repaint_phases()

    def start_execute(self, steps=None, at=None):
        """
        The Plan is approved: Plan is done, Execute is running. The foreseen Steps
are only built here, not at `start_plan`, since a Plan still open for
editing has not foreseen anything yet, and *steps* overrides what
`start_plan` foresaw with what the Gate actually approved when the two
differ, because the Plan Gate is edited directly, so what runs is not
always what `start_plan` was shown.
"""
        self._timed(at, PLAN, EXECUTE)
        if self._states[PLAN] != FAILED:
            self._states[PLAN] = DONE
        self._states[EXECUTE] = RUNNING
        if steps is not None:
            self._foreseen = list(steps)
        self._build_steps()
        self._steps_container.setVisible(True)
        self._repaint_phases()

    def step_started(self, index, at=None):
        """1-based. Every step still Running before *index* has already been reported Done."""
        self._timed(at)
        if at is not None:
            self._step_start_times[index] = at

        for position in range(len(self._step_states)):
            if position < index - 1 and self._step_states[position] == RUNNING:
                self._set_step_state(position, DONE)
        if 1 <= index <= len(self._step_states):
            self._set_step_state(index - 1, RUNNING)

    def step_applied(self, index, at=None):
        self._timed(at)
        if at is not None:
            self._step_end_times[index] = at

        self._set_step_state(index - 1, DONE)
        if index == len(self._step_states):
            self.start_assertion(at=at)

    def step_unbuilt(self, index, at=None):
        """Nothing in the geometry was waiting on it, so it counts as reached, not failed."""
        self._timed(at)
        if at is not None:
            self._step_end_times[index] = at

        self._set_step_state(index - 1, DONE)
        if index == len(self._step_states):
            self.start_assertion(at=at)

    def step_failed(self, index, at=None):
        """1-based: an attempt at *index* did not build and the loop retries, so the
Step is marked failed and nothing else moves. A Step retried three times
behind an unchanged Queue is a stalled run from the outside, since two
failed attempts on Step 4 once left a Queue still showing it running. The
loop does not re-announce a retried Step, so the mark stays failed until
`step_applied` turns it Done if an attempt lands, meaning a Step that
recovers does not stay red.
"""
        self._timed(at)
        self._set_step_state(index - 1, FAILED)

    def start_assertion(self, at=None):
        """Every Step has reached a terminal state: Execute is done, Assertion is running."""
        self._timed(at, EXECUTE, ASSERTION)
        if self._states[EXECUTE] != FAILED:
            self._states[EXECUTE] = DONE
        self._states[ASSERTION] = RUNNING
        self._steps_container.setVisible(self._states[EXECUTE] == FAILED)
        self._repaint_phases()

    def start_review(self, at=None):
        """The acceptance checks have a verdict: Assertion is done, Review is running."""
        self._timed(at, ASSERTION, REVIEW)
        if self._states[ASSERTION] != FAILED:
            self._states[ASSERTION] = DONE
        self._states[REVIEW] = RUNNING
        self._repaint_phases()

    def finish_review(self, at=None):
        self._timed(at, REVIEW)
        if self._states[REVIEW] != FAILED:
            self._states[REVIEW] = DONE
        self._repaint_phases()

    def call_started(self, phase, ceiling, event_time):
        self._current_call_start = event_time
        self._current_call_ceiling = ceiling
        self._current_call_phase = phase
        self._latest_event_time = event_time
        if self._live:
            self._repaint_phases()

    def call_ended(self, event_time):
        self._current_call_start = None
        self._current_call_ceiling = None
        self._current_call_phase = None
        self._latest_event_time = event_time
        if self._live:
            self._repaint_phases()

    def fail_current(self, at=None):
        """
        Whichever phase was running when the run stopped, stops running as
        Failed -- and its clock stops there too: a phase that ended in a
        failure took as long as it took, and a row still counting up under a
        stopped run is the one thing on this widget that would keep moving
        after everything else had.
        """
        for phase in PHASES:
            self._timed(at, phase)
        for phase in PHASES:
            if self._states[phase] == RUNNING:
                self._states[phase] = FAILED
        if self._states[EXECUTE] == FAILED:
            for position in range(len(self._step_states)):
                if self._step_states[position] == RUNNING:
                    self._set_step_state(position, FAILED)
            self._steps_container.setVisible(True)
        self._repaint_phases()

    def showEvent(self, event):
        """
        Icons are tinted once the widget is on screen: a widget carries the global
stylesheet's colour only after Qt has polished it, and asked in `__init__`
it still answers with the OS palette, which is black under every FreeCAD
dark theme.
        """
        super().showEvent(event)
        self._repaint_phases()
        for position in range(len(self._step_states)):
            self._repaint_step(position)

    def hideEvent(self, event):
        """
        The spinner is stopped rather than left ticking behind a hidden
        panel -- this Queue is pinned for as long as a run takes, up to
        45 minutes, and nothing reads a QTimer firing off screen.
        """
        super().hideEvent(event)
        self._spin_timer.stop()

    def _build_steps(self):
        for row in self._step_rows:
            row.setParent(None)
        self._step_rows = []
        self._step_icons = []
        self._step_labels = []
        self._step_states = []
        for description in self._foreseen:
            row, icon, label = _row(self._steps_area, str(description), ICON_DENSE, step=True)
            self._step_rows.append(row)
            self._step_icons.append(icon)
            self._step_labels.append(label)
            self._step_states.append(QUEUED)
        for position in range(len(self._step_states)):
            self._repaint_step(position)

    def _set_step_state(self, position, state):
        if not (0 <= position < len(self._step_states)):
            return
        self._step_states[position] = state
        self._repaint_step(position)
        self._paint_head()

    def _repaint_phases(self):
        """Repaint all phase rows with current state and elapsed time.
        RUNNING shows finished passes' total plus the one in flight; DONE
        shows the sum of every pass, folded into `_phase_totals` by
        `_timed`/`start_plan` as each closed. Times come from event.at
        deltas so replayed sessions match the live run.
        """
        for phase in PHASES:
            label_text = _LABEL[phase]

            if self._states[phase] == DONE and phase in self._phase_totals:
                elapsed = self._phase_totals[phase]
                label_text = f"{_LABEL[phase]}  {_format_time(elapsed)}"

            elif self._states[phase] == RUNNING:
                start = self._phase_start_times.get(phase)
                if start is not None and self._latest_event_time is not None:
                    elapsed = self._latest_event_time - start + self._phase_totals.get(phase, 0)
                    label_text = f"{_LABEL[phase]}  {_format_time(elapsed)}"

                    if (self._current_call_start is not None and 
                        self._current_call_phase == phase):
                        call_elapsed = self._latest_event_time - self._current_call_start
                        if self._current_call_ceiling is not None:
                            ceiling_str = _format_time(self._current_call_ceiling)
                            label_text += f" · call {_format_time(call_elapsed)} / {ceiling_str}"
                        else:
                            label_text += f" · call {_format_time(call_elapsed)}"

            self._text_labels[phase].setText(label_text)
            _paint(self._icon_labels[phase], self._text_labels[phase],
                  self._states[phase], ICON)

        self._paint_running_mark()
        self._paint_head()
        self._sync_spin_timer()
        if self._open:
            self._fit_open_height()

    def _paint_head(self):
        """The folded line: the running phase, or the last to have moved once
        nothing is.
        A stopped run still has to say where it stopped, so a finished or
        failed Queue keeps the phase it ended on. Open, the list below
        already names which phase runs, so the head names the disclosure.
        """
        if self._open:
            self._head_icon.setPixmap(QtGui.QPixmap())
            self._head_label.setText("Queue")
            self._head_label.setStyleSheet(css(BODY))
        else:
            running = [phase for phase in PHASES if self._states[phase] == RUNNING]
            moved = [phase for phase in PHASES if self._states[phase] != QUEUED]
            phase = running[0] if running else (moved[-1] if moved else PHASES[0])
            _paint(self._head_icon, self._head_label, self._states[phase], ICON)
            self._head_label.setText(_LABEL[phase] + self._step_count_said())
        icon = UIHelpers.get_icon("chevron_down", size=ICON_DENSE, on=self._head_label)
        if not self._open:
            icon = QtGui.QIcon(icon.pixmap(ICON_DENSE, ICON_DENSE).transformed(
                QtGui.QTransform().rotate(-90), QtCore.Qt.SmoothTransformation))
        self._chevron.setPixmap(icon.pixmap(ICON_DENSE, ICON_DENSE))

    def _paint_running_mark(self):
        """
        The running phase's mark in the open list, spun rather than left
        static -- a phase that takes four minutes has to look alive. The
        container and its text keep the state they always had (`_paint`);
        only this glyph is ever replaced.
        """
        for phase in PHASES:
            if self._states[phase] == RUNNING:
                self._icon_labels[phase].setPixmap(self._spin_pixmap())

    def _spin_pixmap(self):
        """
        The running mark, rotated a `QTimer` tick at a time from one cached,
        already-tinted pixmap -- re-reading and re-rendering `refresh.svg`
        every 100ms is the one thing this cannot afford across a run that
        pins the same phase running for 45 minutes.
        """
        if self._spin_base is None:
            icon = UIHelpers.get_icon("refresh", size=ICON_DENSE, on=self, colour=RUNNING_COLOUR)
            self._spin_base = icon.pixmap(ICON_DENSE, ICON_DENSE)
        spun = QtGui.QPixmap(ICON_DENSE, ICON_DENSE)
        spun.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(spun)
        painter.translate(ICON_DENSE / 2.0, ICON_DENSE / 2.0)
        painter.rotate(self._spin_angle)
        painter.translate(-ICON_DENSE / 2.0, -ICON_DENSE / 2.0)
        painter.drawPixmap(0, 0, self._spin_base)
        painter.end()
        return spun

    def _spin_tick(self):
        self._spin_angle = (self._spin_angle + 30) % 360
        self._paint_running_mark()

    def _sync_spin_timer(self):
        """Ticking costs nothing alone, but this panel is pinned for the 45
        minutes a run takes, so a timer behind a hidden or finished panel
        runs for nothing. The cached pixmap drops on every start: tinted
        from *self*'s palette, correct only once Qt has polished it, true
        right after a `showEvent`.
        """
        running = any(state == RUNNING for state in self._states.values())
        if running and self.isVisible():
            if not self._spin_timer.isActive():
                self._spin_base = None
                self._spin_timer.start()
        else:
            self._spin_timer.stop()

    def _fit_open_height(self):
        """
        The open list's height is its rows' own height, capped at `CEILING`
        rather than fixed to it -- a short list stops short, and only a list
        taller than the ceiling scrolls.
        """
        content = self._all.widget().sizeHint().height()
        ceiling = int(self.window().height() * self.CEILING)
        self._all.setFixedHeight(min(content, ceiling))

    def _step_count_said(self):
        """`Execute` alone is not a position; the Step it is on is."""
        if self._states[EXECUTE] != RUNNING or not self._step_states:
            return ""
        running = [position for position, state in enumerate(self._step_states)
                   if state in (RUNNING, FAILED)]
        if not running:
            return ""
        return "  ·  Step %d/%d" % (running[0] + 1, len(self._step_states))

    def _repaint_step(self, position):
        """
        Repaint one Step row with current state and elapsed time: a RUNNING step
shows live elapsed time from `STEP_STARTED`, and a completed step,
`APPLIED` or `UNBUILT`, shows its total duration. Times are from `event.at`
deltas.
"""
        label_text = str(self._foreseen[position]) if position < len(self._foreseen) else ""
        state = self._step_states[position]
        step_num = position + 1

        if state in (DONE, FAILED) and step_num in self._step_end_times:
            start = self._step_start_times.get(step_num)
            end = self._step_end_times[step_num]
            if start is not None:
                elapsed = end - start
                label_text = f"{step_num}. {self._foreseen[position]}  {_format_time(elapsed)}"
            else:
                label_text = f"{step_num}. {self._foreseen[position]}"

        elif state == RUNNING and step_num in self._step_start_times:
            start = self._step_start_times[step_num]
            if self._latest_event_time is not None:
                elapsed = self._latest_event_time - start
                label_text = f"{step_num}. {self._foreseen[position]}  {_format_time(elapsed)}"
            else:
                label_text = f"{step_num}. {self._foreseen[position]}"
        else:
            label_text = f"{step_num}. {self._foreseen[position]}"

        self._step_labels[position].setText(label_text)
        _paint(self._step_icons[position], self._step_labels[position],
              self._step_states[position], ICON_DENSE)
