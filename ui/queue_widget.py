"""
The Queue: the run's five macro-phases, Execute's foreseen Steps nested
beneath it (FCC-169, FCC-172).

Purely a renderer, in `PlanWidget`'s shape: told what happened and never asks
the loop anything. The five phase keys mirror `core.loop`'s DESIGN_PHASE /
PLAN_PHASE / EXECUTE_PHASE / ASSERTION_PHASE / REVIEW_PHASE by value, kept
local rather than imported so this widget stays as free of `core` as
`plan_widget.py` is.

No `phase_started`/`phase_ended` event reaches the wire (`core/events.py`), so
every transition here is read off a structural event that already carries the
fact: `PLAN`/`PLAN_REVISED` says Design finished, an approved `GATE` says Plan
finished, a Step's own terminal event (`APPLIED`/`UNBUILT`) at the last index
says Execute finished, `ASSERTION` says Assertion finished, `REVIEW_VERDICT`
says Review finished. Each of those doubles as the *next* phase's Running
mark, since nothing else announces one starting. Preflight is not a sixth
row -- it runs per-Step inside Execute alongside Apply.
"""
try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide import QtCore, QtGui
    QtWidgets = QtGui

from ui.design_tokens import FAIL, RUNNING as RUNNING_COLOUR, SPACE_MD, SPACE_SM, SPACE_XS
from ui.typography import BODY, BODY_STRONG, css
from ui.ui_helpers import UIHelpers

DESIGN = "design"
PLAN = "plan"
EXECUTE = "execute"
ASSERTION = "assertion"
REVIEW = "review"

PHASES = (DESIGN, PLAN, EXECUTE, ASSERTION, REVIEW)

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

_ICON = {QUEUED: "circle", RUNNING: "circle_dot", DONE: "circle_check", FAILED: "circle_x"}

_LABEL = {DESIGN: "Design", PLAN: "Plan", EXECUTE: "Execute",
          ASSERTION: "Assertion", REVIEW: "Review"}

_MARK_COLOUR = {RUNNING: RUNNING_COLOUR, FAILED: FAIL}


def _row(layout, text, size):
    """One icon + text row, added to *layout*, as the pair its state is painted onto."""
    container = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(container)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(SPACE_XS)
    icon = QtWidgets.QLabel()
    label = QtWidgets.QLabel(text)
    row.addWidget(icon)
    row.addWidget(label)
    row.addStretch(1)
    layout.addWidget(container)
    return container, icon, label


def _paint(icon_label, text_label, state, size):
    icon_label.setPixmap(
        UIHelpers.get_icon(_ICON[state], size=size, on=text_label).pixmap(size, size))
    style = css(BODY_STRONG if state == RUNNING else BODY)
    colour = _MARK_COLOUR.get(state)
    if colour:
        style = "color:%s; %s" % (colour, style)
    text_label.setStyleSheet(style)


class QueueWidget(QtWidgets.QFrame):
    """
    One row per phase, Execute's foreseen Steps nested under its row.

    A phase already `FAILED` never moves again on its own: the run has
    stopped, and every method below leaves a `FAILED` phase where it is
    rather than carrying it forward as if the run were still going.
    """

    def __init__(self):
        super().__init__()
        self.setObjectName("QueueCard")

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(SPACE_MD, SPACE_SM, SPACE_MD, SPACE_SM)
        outer.setSpacing(SPACE_XS)

        self._states = {phase: QUEUED for phase in PHASES}
        self._states[DESIGN] = RUNNING
        self._icon_labels = {}
        self._text_labels = {}
        self._foreseen = []
        self._step_states = []
        self._step_rows = []
        self._step_icons = []
        self._step_labels = []

        for phase in PHASES:
            _, icon, label = _row(outer, _LABEL[phase], 16)
            self._icon_labels[phase] = icon
            self._text_labels[phase] = label
            if phase == EXECUTE:
                self._steps_container = QtWidgets.QWidget()
                self._steps_area = QtWidgets.QVBoxLayout(self._steps_container)
                self._steps_area.setContentsMargins(SPACE_MD, 0, 0, 0)
                self._steps_area.setSpacing(2)
                self._steps_container.setVisible(False)
                outer.addWidget(self._steps_container)

        self._repaint_phases()

    def phase_state(self, phase):
        return self._states[phase]

    def step_state(self, index):
        """1-based, matching the Step numbering in the event stream."""
        return self._step_states[index - 1]

    def step_count(self):
        return len(self._step_states)

    def start_plan(self, steps):
        """Design is done and Plan is running -- the first Plan, or a revision of one."""
        if self._states[DESIGN] != FAILED:
            self._states[DESIGN] = DONE
        if self._states[PLAN] != FAILED:
            self._states[PLAN] = RUNNING
        for phase in (EXECUTE, ASSERTION, REVIEW):
            if self._states[phase] != FAILED:
                self._states[phase] = QUEUED
        self._foreseen = list(steps)
        self._steps_container.setVisible(False)
        self._repaint_phases()

    def start_execute(self, steps=None):
        """
        The Plan is approved: Plan is done, Execute is running.

        The foreseen Steps are only built here, not at `start_plan` -- a Plan
        still open for editing has not foreseen anything yet. *steps*
        overrides what `start_plan` foresaw with what the Gate actually
        approved, when the two differ (FCC-177: the Plan Gate is edited
        directly, so what runs is not always what `start_plan` was shown).
        """
        if self._states[PLAN] != FAILED:
            self._states[PLAN] = DONE
        self._states[EXECUTE] = RUNNING
        if steps is not None:
            self._foreseen = list(steps)
        self._build_steps()
        self._steps_container.setVisible(True)
        self._repaint_phases()

    def step_started(self, index):
        """1-based. Every step still Running before *index* has already been reported Done."""
        for position in range(len(self._step_states)):
            if position < index - 1 and self._step_states[position] == RUNNING:
                self._set_step_state(position, DONE)
        if 1 <= index <= len(self._step_states):
            self._set_step_state(index - 1, RUNNING)

    def step_applied(self, index):
        self._set_step_state(index - 1, DONE)
        if index == len(self._step_states):
            self.start_assertion()

    def step_unbuilt(self, index):
        """Nothing in the geometry was waiting on it, so it counts as reached, not failed."""
        self._set_step_state(index - 1, DONE)
        if index == len(self._step_states):
            self.start_assertion()

    def start_assertion(self):
        """Every Step has reached a terminal state: Execute is done, Assertion is running."""
        if self._states[EXECUTE] != FAILED:
            self._states[EXECUTE] = DONE
        self._states[ASSERTION] = RUNNING
        self._steps_container.setVisible(self._states[EXECUTE] == FAILED)
        self._repaint_phases()

    def start_review(self):
        """The acceptance checks have a verdict: Assertion is done, Review is running."""
        if self._states[ASSERTION] != FAILED:
            self._states[ASSERTION] = DONE
        self._states[REVIEW] = RUNNING
        self._repaint_phases()

    def finish_review(self):
        if self._states[REVIEW] != FAILED:
            self._states[REVIEW] = DONE
        self._repaint_phases()

    def fail_current(self):
        """Whichever phase was running when the run stopped, stops running as Failed."""
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
        Icons are tinted once the widget is on screen.

        A widget carries the global stylesheet's colour only after Qt has
        polished it; asked in `__init__` it still answers with the OS
        palette, which is black under every FreeCAD dark theme (ADR-0011).
        """
        super().showEvent(event)
        self._repaint_phases()
        for position in range(len(self._step_states)):
            self._repaint_step(position)

    def _build_steps(self):
        for row in self._step_rows:
            row.setParent(None)
        self._step_rows = []
        self._step_icons = []
        self._step_labels = []
        self._step_states = []
        for description in self._foreseen:
            row, icon, label = _row(self._steps_area, str(description), 14)
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

    def _repaint_phases(self):
        for phase in PHASES:
            _paint(self._icon_labels[phase], self._text_labels[phase],
                  self._states[phase], 16)

    def _repaint_step(self, position):
        _paint(self._step_icons[position], self._step_labels[position],
              self._step_states[position], 14)
