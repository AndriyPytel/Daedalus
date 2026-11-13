"""
The panel, built for real against FreeCAD's own Qt binding, offscreen.

Most of the panel stays Level 3 — how a widget *looks* under a theme is not
something a test can tell you.
What is checked here is the arithmetic and the state machine that would
otherwise be verified by opening the panel and squinting: the composer's
height, Send and Stop sharing one slot, and Sessions surviving a restart —
restored, renamed, deleted, and let go of when the user switches away.

The bound itself — pruning on create, and a promptless launch counting for
nothing — is Level 1 now that the Sessions are not behind a widget. See
tests/test_sessions.py; what stays here is the stack keeping step with them.
"""
import os
import threading
import time

import pytest

from conftest import NO_QT, QT_APP

pytestmark = pytest.mark.freecad


def test_the_qt_modules_all_come_from_one_binding():
    """
    Every test below takes Qt from `conftest`, not from a binding it names.
    Half a migration — QtCore from one package, QtWidgets from another — would
    build widgets that cannot see each other's types, and it would show up as
    something unrelated failing. Assert the export contract instead.
    """
    from conftest import QtCore, QtGui, QtWidgets

    if QT_APP is None:
        pytest.skip(NO_QT)

    bindings = {module.__name__.split(".")[0] for module in (QtCore, QtGui, QtWidgets)}
    assert len(bindings) == 1, f"conftest handed out Qt from more than one binding: {bindings}"
    assert isinstance(QT_APP, QtWidgets.QApplication), \
        "the suite's QApplication is not the one conftest's QtWidgets would build"


@pytest.fixture(scope="module")
def sessions_are_the_tests_own():
    """
    Sessions are durable now, so building a panel writes to the real settings.
    The developer's own history is not the suite's to spend.
    """
    if QT_APP is None:
        pytest.skip(NO_QT)
    from bridge import settings_manager

    settings = settings_manager.instance()
    theirs = settings.get_sessions()
    settings.set_sessions([])
    yield
    for made in settings.get_sessions():
        try:
            os.remove(made["log"])
        except OSError:
            pass
    settings.set_sessions(theirs)


@pytest.fixture(scope="module")
def panel(sessions_are_the_tests_own):
    app = QT_APP
    if app is None:
        pytest.skip(NO_QT)
    from ui.daedalus_panel import DaedalusPanel

    built = DaedalusPanel()
    built.resize(360, 700)
    built.show()
    app.processEvents()
    yield built
    built._cleanup_thread()


def test_the_empty_composer_is_one_row(panel):
    """
    It used to be frozen at three, taking vertical space from the chat.

    "One row" is asserted as the difference a second row makes, rather than
    against a formula — a pixel count copied from the implementation would
    pass however wrong the implementation was.
    """
    from conftest import QtGui

    panel.user_input.clear()
    empty = panel.user_input.height()
    panel.user_input.setPlainText("x\nx")
    two_rows = panel.user_input.height()

    line = QtGui.QFontMetrics(panel.user_input.font()).lineSpacing()
    assert two_rows - empty == pytest.approx(line, abs=2)


def test_the_composer_grows_with_the_text_then_scrolls(panel):
    """
    Past the ceiling it must scroll. Frozen at three rows with scrollbars off,
    as it was, anything past the third row was simply unreachable.
    """
    from conftest import QtCore

    panel.user_input.clear()
    one_row = panel.user_input.height()

    panel.user_input.setPlainText("x\n" * 3)
    assert one_row < panel.user_input.height() <= int(panel.height() * 0.4)

    panel.user_input.setPlainText("x\n" * 200)
    assert panel.user_input.height() == int(panel.height() * 0.4)
    assert panel.user_input.verticalScrollBarPolicy() == QtCore.Qt.ScrollBarAsNeeded

    panel.user_input.clear()
    assert panel.user_input.verticalScrollBarPolicy() == QtCore.Qt.ScrollBarAlwaysOff


def test_send_stays_while_a_run_is_going(panel):
    """
    Stop stays up for the length of a run in flight, beside Send.
    """
    panel._set_running(True)
    assert (panel.stop_button.isVisible(), panel.send_button.isVisible()) == (True, True)

    panel._set_running(False)
    assert (panel.stop_button.isVisible(), panel.send_button.isVisible()) == (False, True)


def test_abort_shows_alongside_stop_while_a_run_is_going(panel):
    """Abort is a second way to end the run in flight, not a replacement for Stop."""
    panel._set_running(True)
    assert (panel.stop_button.isVisible(), panel.abort_button.isVisible()) == (True, True)

    panel._set_running(False)
    assert (panel.stop_button.isVisible(), panel.abort_button.isVisible()) == (False, False)


def test_the_composer_shows_the_figma_placeholder(panel):
    assert panel.user_input.placeholderText() == "Describe what you want to build\u2026"


def test_the_composer_stays_usable_while_a_run_is_going(panel):
    """
    A run holds the Document, not the conversation. Darkened, the composer
    also took the Gate's answer and the next Intent with it, and the user was
    left watching a run they could not add to.
    """
    panel._set_running(True)

    assert panel.user_input.isEnabled()
    assert panel.user_input.graphicsEffect() is None

    panel._set_running(False)
    assert panel.user_input.isEnabled()


def test_a_second_intent_on_a_running_document_is_refused(panel, monkeypatch):
    """
    One file, one Intent: a second Intent sent while this Session's
    Document already has a run in flight is refused at the composer, with a
    reason, rather than queued behind it.
    """
    import types

    session_id = panel.chat_sessions.current_id()
    bundle = panel._worker_for(session_id)
    monkeypatch.setattr(panel, "_bind_document", lambda bundle: None)
    sent = []
    bundle.request_work.connect(lambda *args: sent.append(args[0]))

    panel.user_input.setPlainText("make the base plate")
    panel.on_send()
    QT_APP.processEvents()
    panel.user_input.setPlainText("and a lug on the right")
    panel.on_send()
    QT_APP.processEvents()

    assert sent == ["make the base plate"], "the second Intent must never start a run"
    assert panel.user_input.toPlainText() == "and a lug on the right", \
        "a refused Intent is left in the composer, not thrown away"
    assert "run in flight" in _said_in(panel)

    panel.on_run_finished(types.SimpleNamespace(ok=True), panel.sessions.at(session_id)["log"])

    assert (bundle.running, panel._running) == (False, False)

    panel.on_send()
    QT_APP.processEvents()
    assert sent == ["make the base plate", "and a lug on the right"], \
        "the same Intent sends once the Document's run has ended"


def test_two_sessions_on_the_same_document_cannot_both_run(panel, monkeypatch):
    """
    One file, one Intent holds across Sessions, not only within one: a second
    Session bound to the same Document the first is still running against is
    refused too, and neither of its `snapshot`/`lock` calls is ever
    reached -- `on_send` never gets past `_document_busy`.
    """
    import types

    from ui.daedalus_panel import _SessionWorker, EventBridge, AskTheUser

    first = panel._worker_for(panel.chat_sessions.current_id())
    monkeypatch.setattr(panel, "_bind_document", lambda bundle: None)
    same_document = object()
    first.worker._document = same_document

    second = _SessionWorker(EventBridge(), AskTheUser())
    second.worker._document = same_document
    panel._workers["second-session"] = second

    first.running = True
    assert panel._document_busy(second) is True

    first.running = False
    assert panel._document_busy(second) is False

    second.shut_down(grace_ms=100)
    del panel._workers["second-session"]


@pytest.mark.parametrize("status", [
    "Step 1/9: Create a sketch on the XY plane and draw the two circles at the computed centre",
    "Thinking: " + "the plan looks reasonable so I will start from the base sketch " * 2,
    "Applied → " + "A" * 300,
])
def test_a_long_line_on_the_queue_does_not_widen_the_panel(panel, status):
    """
    What the Queue says is happening now is outside the transcript's
    QScrollArea, so its minimum width is the panel's. Unwrapped, a Step
    description made the dock wider — and Qt does not give the width back when
    the text goes short again, so the user lost the dock width they had set.
    Inherited by the Queue from the status line it replaced.

    The dock's own minimumSizeHint only follows its child once shown, which
    the fixture's panel is not. The child is where the width is decided.
    """
    contents = panel.widget()
    panel._queue_widget.now("Working…")
    narrow = contents.minimumSizeHint().width()

    panel._queue_widget.now(status)

    assert contents.minimumSizeHint().width() == narrow, \
        "the panel's minimum width follows what the Queue says"


def test_the_empty_chat_says_where_the_user_stands(panel):
    """
    Freshly built rather than `panel._empty_state` itself: the model fetch
    that landed since the panel opened may already have moved the button off
    whatever it showed when the transcript's own empty state was captured,
    and that race is not what this test is for.
    """
    text = panel._build_empty_state().text()
    assert "Describe what to build" in text
    assert panel.model_button.text().strip() in text


def _selector_click(popup, text):
    from ui.model_selector import _Row

    for i in range(popup._list_layout.count()):
        widget = popup._list_layout.itemAt(i).widget()
        if isinstance(widget, _Row) and widget.text == text:
            widget.clicked.emit()
            return
    raise AssertionError("no row %r in the selector's current step" % text)


_TWO_PROVIDERS = [
    {"id": "gpt-4o", "display": "GPT-4o", "provider": "OpenAI"},
    {"id": "o1", "display": "o1", "provider": "OpenAI"},
    {"id": "claude-opus-4", "display": "Claude Opus 4", "provider": "Anthropic"},
]


def _mock_the_pills(monkeypatch, model="", provider="", effort="medium"):
    """
    The developer's own machine has a real last model, in real FreeCAD
    settings, that has nothing to do with `_TWO_PROVIDERS` below — read it
    back and `_refresh_models` reaches for `_retire_model`, which would
    overwrite that real setting with one of this test's fake ids. The two
    settings the pills read and write are handed back to whoever wants to
    assert on them.
    """
    import sdk

    stored = {"model": (provider, model), "effort": effort}
    monkeypatch.setattr(sdk, "get_last_model", lambda: stored["model"])
    monkeypatch.setattr(sdk, "set_last_model",
                        lambda provider, model: stored.__setitem__("model", (provider, model)))
    monkeypatch.setattr(sdk, "get_effort", lambda: stored["effort"])
    monkeypatch.setattr(sdk, "set_effort", lambda level: stored.__setitem__("effort", level))
    return stored


def test_the_composer_carries_the_effort_in_force(panel, monkeypatch):
    """How hard the model thinks is a choice made where the model is chosen, so it can be changed between two runs and compared."""
    _mock_the_pills(monkeypatch, effort="high")

    panel._show_effort()

    assert panel.effort_button.text() == "high"


def test_picking_an_effort_stores_it_and_shows_it(panel, monkeypatch):
    """The pill says what the next run will be asked for, not what was stored."""
    stored = _mock_the_pills(monkeypatch)

    panel._on_effort_selected("high")

    assert stored["effort"] == "high"
    assert panel.effort_button.text() == "high"


def test_picking_a_model_stores_it_with_its_provider(panel, monkeypatch):
    """A model is only resolvable through the provider that offers it, so the pair is what is kept."""
    stored = _mock_the_pills(monkeypatch)
    panel._refresh_models(_TWO_PROVIDERS)

    panel._on_model_selected("Anthropic", "claude-opus-4")

    assert stored["model"] == ("Anthropic", "claude-opus-4")


def test_a_model_offered_by_two_providers_is_stored_with_the_one_picked(panel, monkeypatch):
    stored = _mock_the_pills(monkeypatch)
    both = _TWO_PROVIDERS + [{"id": "gpt-4o", "display": "GPT-4o", "provider": "OpenRouter"}]
    panel._refresh_models(both)

    panel._on_model_selected("OpenRouter", "gpt-4o")

    assert stored["model"] == ("OpenRouter", "gpt-4o")


def test_the_model_the_pill_falls_back_to_is_the_model_the_run_gets(panel, monkeypatch):
    """Nothing stored: the pill shows the first model on offer, and stores it, since the run reads what is stored and nothing else."""
    stored = _mock_the_pills(monkeypatch)

    panel._refresh_models(_TWO_PROVIDERS)

    assert panel._current_model_id == "gpt-4o"
    assert stored["model"] == ("OpenAI", "gpt-4o")


def test_a_fresh_refresh_does_not_overwrite_a_model_chosen_last_session(panel, monkeypatch):
    """A stored pick already matching a live model must not be touched by a refresh that only recomputes what the pill shows."""
    stored = _mock_the_pills(monkeypatch, model="claude-opus-4", provider="Anthropic")

    panel._refresh_models(_TWO_PROVIDERS)

    assert stored["model"] == ("Anthropic", "claude-opus-4")
    assert panel._current_model_id == "claude-opus-4"


def test_an_effort_pill_is_never_disabled(panel, monkeypatch):
    """The Effort is the run's, whatever the model: a pill that goes grey is a pill that says some runs ignore it."""
    _mock_the_pills(monkeypatch)
    panel._refresh_models(_TWO_PROVIDERS)

    assert panel.effort_button.isEnabled()


def test_clicking_the_model_button_opens_the_provider_step(panel, monkeypatch):
    """The popup opens on a flat list of providers, not their models."""
    from ui.model_selector import PROVIDER_STEP

    _mock_the_pills(monkeypatch)
    panel._refresh_models(_TWO_PROVIDERS)
    panel._open_model_selector()
    QT_APP.processEvents()

    assert panel.model_selector.isVisible()
    assert panel.model_selector.step == PROVIDER_STEP
    assert panel.model_selector.visible_rows() == ["OpenAI", "Anthropic"]
    panel.model_selector.close()


def test_choosing_a_provider_replaces_the_list_with_its_models(panel, monkeypatch):
    """Not grouping — sequence: the provider list is gone once its models are up."""
    from ui.model_selector import MODEL_STEP

    _mock_the_pills(monkeypatch)
    panel._refresh_models(_TWO_PROVIDERS)
    panel._open_model_selector()
    QT_APP.processEvents()

    _selector_click(panel.model_selector, "OpenAI")
    QT_APP.processEvents()

    assert panel.model_selector.step == MODEL_STEP
    assert panel.model_selector.visible_rows() == ["GPT-4o", "o1"]
    panel.model_selector.close()


def test_the_picker_opens_above_the_button_that_opens_it(panel, monkeypatch):
    """
    The trigger is at the bottom of a docked panel, so a list that
    drops down drops off the screen.
    """
    from conftest import QtCore

    _mock_the_pills(monkeypatch)
    panel._refresh_models(_TWO_PROVIDERS)
    panel._open_model_selector()
    QT_APP.processEvents()

    popup = panel.model_selector
    button_top = panel.model_button.mapToGlobal(QtCore.QPoint(0, 0)).y()
    assert popup.geometry().bottom() <= button_top
    popup.close()


def test_a_picker_with_more_models_than_room_scrolls_inside_itself(panel, monkeypatch):
    """
    The list is capped by the room the screen has, and what does not
    fit is scrolled to — never a popup taller than the display it is on.
    """
    _mock_the_pills(monkeypatch)
    panel._refresh_models([{"id": "m%d" % n, "display": "Model %d" % n,
                            "provider": "OpenAI"} for n in range(200)])
    panel._open_model_selector()
    QT_APP.processEvents()
    _selector_click(panel.model_selector, "OpenAI")
    QT_APP.processEvents()

    popup = panel.model_selector
    popup._reflow()
    room = popup.screen().availableGeometry()
    assert popup.height() <= room.height()
    assert popup.geometry().top() >= room.top()
    assert popup._scroll.verticalScrollBar().maximum() > 0
    popup.close()


def test_choosing_a_model_closes_the_popup_and_updates_the_panel(panel, monkeypatch):
    stored = _mock_the_pills(monkeypatch)

    panel._refresh_models(_TWO_PROVIDERS)
    panel._open_model_selector()
    QT_APP.processEvents()
    _selector_click(panel.model_selector, "Anthropic")
    QT_APP.processEvents()
    _selector_click(panel.model_selector, "Claude Opus 4")
    QT_APP.processEvents()

    assert not panel.model_selector.isVisible()
    assert panel._current_model_id == "claude-opus-4"
    assert panel.model_button.text() == "Claude Opus 4"
    assert stored["model"] == ("Anthropic", "claude-opus-4")


def test_reopening_the_selector_always_starts_at_the_provider_step(panel, monkeypatch):
    """
    Reopening after a model is picked shows providers again, not the
    last-selected provider's models (an Acceptance Criterion).
    """
    from ui.model_selector import PROVIDER_STEP

    _mock_the_pills(monkeypatch)

    panel._refresh_models(_TWO_PROVIDERS)
    panel._open_model_selector()
    QT_APP.processEvents()
    _selector_click(panel.model_selector, "OpenAI")
    QT_APP.processEvents()
    _selector_click(panel.model_selector, "GPT-4o")
    QT_APP.processEvents()

    panel._open_model_selector()
    QT_APP.processEvents()

    assert panel.model_selector.step == PROVIDER_STEP
    assert panel.model_selector.visible_rows() == ["OpenAI", "Anthropic"]
    panel.model_selector.close()


def test_the_picker_has_no_window_frame(panel, monkeypatch):
    """
    No border around the popup — a real OS window frame would be
    one no theme colour or ``design_tokens`` radius could match, since
    the platform paints it, not the theme.
    """
    from conftest import QtCore

    _mock_the_pills(monkeypatch)
    panel._refresh_models(_TWO_PROVIDERS)
    panel._open_model_selector()
    QT_APP.processEvents()

    flags = panel.model_selector.windowFlags()
    assert flags & QtCore.Qt.Popup
    assert flags & QtCore.Qt.FramelessWindowHint
    panel.model_selector.close()


def test_neither_popup_draws_a_border(panel, monkeypatch):
    """
    The user asked twice for no border around the model picker, and
    the same read applies to the history popup — the opaque fill and its
    radius are the whole edge either popup has, not a hairline on top of it.
    """
    _mock_the_pills(monkeypatch)
    panel._refresh_models(_TWO_PROVIDERS)
    panel._open_model_selector()
    QT_APP.processEvents()
    assert "border:" not in panel.model_selector._container.styleSheet()
    panel.model_selector.close()

    panel.toggle_history()
    QT_APP.processEvents()
    assert "border:" not in panel.history_popup._container.styleSheet()
    panel.history_popup.close()


def test_the_popups_own_fill_is_opaque_and_comes_from_the_anchor_theme(panel, monkeypatch):
    """
    A ``Qt.Popup`` is top-level and has nothing behind it, so a
    translucent fill composites onto whatever the platform hands a bare
    top-level window instead of the docked panel it floats over — white,
    regardless of FreeCAD's own dark theme, which is what the user
    photographed twice. Both popups must be fully opaque and paint the exact
    colour ``UIHelpers.surface_colour`` reads off the widget they open from,
    not a fixed value of their own.

    The sampled pixel sits inside each popup's own margin, left of where its
    first child starts and away from the rounded corners `RADIUS_SURFACE`
    clips — a point guaranteed to be the fill and nothing painted over it.
    """
    from conftest import QtGui
    from ui.ui_helpers import UIHelpers

    _mock_the_pills(monkeypatch)
    panel._refresh_models(_TWO_PROVIDERS)

    original = panel.styleSheet()
    panel.setStyleSheet("QWidget { background-color: #123456; }\n" + original)
    QT_APP.processEvents()
    try:
        expected = UIHelpers.surface_colour(panel.model_button)
        assert expected == "#123456"

        panel._open_model_selector()
        QT_APP.processEvents()
        image = panel.model_selector.grab().toImage()
        fill = QtGui.QColor(image.pixelColor(1, image.height() // 2))
        assert fill.alpha() == 255
        assert fill.name() == expected
        panel.model_selector.close()

        panel.toggle_history()
        QT_APP.processEvents()
        image = panel.history_popup.grab().toImage()
        fill = QtGui.QColor(image.pixelColor(1, image.height() // 2))
        assert fill.alpha() == 255
        assert fill.name() == expected
        panel.history_popup.close()
    finally:
        panel.setStyleSheet(original)
        QT_APP.processEvents()


def test_the_picker_opens_below_when_that_side_has_more_room(monkeypatch):
    """
    The picker opens above by default, but only because the trigger this addon has
    is at the bottom of a docked panel. A trigger near the top of the screen
    has it the other way round, and the measurement in ``_reflow`` is what
    has to notice that, not the direction itself.
    """
    from conftest import QtCore, QtWidgets
    from ui.model_selector import ModelSelectorPopup

    host = QtWidgets.QWidget()
    host.resize(420, 800)
    host.move(0, 0)
    button = QtWidgets.QPushButton("GPT-4o", host)
    button.move(20, 10)
    button.resize(120, 24)
    host.show()
    QT_APP.processEvents()

    popup = ModelSelectorPopup(host)
    popup.open_at(_TWO_PROVIDERS, button)
    QT_APP.processEvents()

    button_bottom = button.mapToGlobal(QtCore.QPoint(0, button.height())).y()
    assert popup.geometry().top() >= button_bottom
    popup.close()
    host.close()


def test_the_current_model_is_marked_by_a_check_not_a_recolour(panel, monkeypatch):
    """
    Rule 1 of the visual grammar's ten: state is never a repaint. The row for
    the model already in use gets a trailing check icon; every other row in
    the same list gets none, and neither gets a stylesheet of its own.
    """
    from ui.model_selector import _Row

    _mock_the_pills(monkeypatch)
    panel._refresh_models(_TWO_PROVIDERS)
    panel.model_button.setText("GPT-4o")
    panel._open_model_selector()
    QT_APP.processEvents()
    _selector_click(panel.model_selector, "OpenAI")
    QT_APP.processEvents()

    rows = {widget.text: widget
           for i in range(panel.model_selector._list_layout.count())
           for widget in [panel.model_selector._list_layout.itemAt(i).widget()]
           if isinstance(widget, _Row)}

    assert rows["GPT-4o"]._check_icon.isVisible()
    assert not rows["o1"]._check_icon.isVisible()
    panel.model_selector.close()


def test_a_row_is_between_control_dense_and_control_tall():
    """
    The visual grammar's row-height range for a compact list, not a value of
    its own picked to look right in one screenshot.
    """
    from ui.design_tokens import CONTROL, CONTROL_DENSE
    from ui.model_selector import _Row

    row = _Row("OpenAI")
    assert CONTROL_DENSE <= row.height() <= CONTROL


def test_the_button_shows_a_stored_model_no_static_list_knows(panel, monkeypatch):
    """
    The stored id lives only in the provider's fetched list, which has not
    landed when the panel is built. Falling through to the first available
    model — GPT-4o — is deliberate.
    """
    from bridge import provider_manager, settings_manager

    monkeypatch.setattr(provider_manager.instance(), "awaiting_model_lists", lambda: True)
    _mock_the_pills(monkeypatch, model="gpt-5.6-sol", provider="OpenAI")

    panel._refresh_models([{"id": "gpt-4o", "display": "GPT-4o", "provider": "OpenAI"}])

    assert panel._current_model_id == "gpt-5.6-sol"


def test_a_failed_fetch_leaves_its_provider_still_awaited(monkeypatch):
    """
    A provider that could not be reached has not said the stored model is gone.
    Treating a failure as an answer drops the user's model on a flaky network.
    """
    from bridge import provider_manager, settings_manager

    manager = provider_manager.instance()
    monkeypatch.setattr(settings_manager.instance(), "get_api_key_for_provider",
                        lambda provider: "sk-test")
    monkeypatch.setattr(manager, "_fetched", {})

    manager._on_fetch_failed(manager.get_all_providers()[0], "no network")

    assert manager.awaiting_model_lists()


def test_a_provider_list_without_the_stored_model_falls_back_and_says_so(panel, monkeypatch):
    """
    Only a real list that has arrived can retire a model — and when it does the
    user is told, because the model they picked is not the one they get.
    """
    from bridge import provider_manager

    stored = _mock_the_pills(monkeypatch, model="gpt-5.6-sol", provider="OpenAI")
    monkeypatch.setattr(provider_manager.instance(), "awaiting_model_lists", lambda: False)

    panel._refresh_models([{"id": "gpt-4o", "display": "GPT-4o", "provider": "OpenAI"}])

    assert panel._current_model_id == "gpt-4o"
    assert stored["model"] == ("OpenAI", "gpt-4o")
    assert panel._queue_widget.isVisible()
    assert "gpt-5.6-sol" in _now_on(panel)
    assert "switched to gpt-4o" in _now_on(panel)


def _never_call_the_real_model(panel):
    """
    Keep `on_send()` from starting a real run, in a test.

    `on_send()` emits the Session's real `request_work`, which is wired to
    `LoopWorker.do_work` and starts an actual run on the worker thread: a real
    document snapshot, and a real call to whichever provider the developer
    machine's own settings name, on their own key or their own subscription's
    CLI, out of a unit test — the very thing
    `the_suite_does_not_talk_to_the_network` exists to stop, except a local
    subscription client shells out rather than opening a socket, so the
    guard never sees it and the call is free to hang. Disconnecting the
    real signal from `do_work` for the
    Session under test, rather than making what `do_work` does harmless,
    keeps the worker thread from ever being asked to do anything at all.

    Not through `monkeypatch`: `do_work` would run off a queued connection
    that `on_send()` only schedules rather than waits for, racing
    `monkeypatch`'s function-scoped undo for whichever lands first — and it
    has been observed losing that race. A `disconnect()` made here takes
    effect immediately and is never undone, but nothing in this module sends
    through this Session again once its test is done with it.

    `_name_session` reaches for `_ask_for_a_title` on the same call, which is
    its own real `_TitleThread` against the same developer settings — a
    second leak `do_work` alone does not stop. Left running, that thread
    outlives this test, and a later test's own `processEvents()` is what
    delivers its queued `titled` signal, landing the hang in whatever test
    happened to be pumping the loop when the real call finally returned. A
    caller that already replaced `_ask_for_a_title` of its own — to watch
    what it was asked — is left alone.
    """
    bundle = panel._worker_for(panel.chat_sessions.current_id())
    bundle.request_work.disconnect(bundle.worker.do_work)
    if "_ask_for_a_title" not in vars(panel):
        panel._ask_for_a_title = lambda log, intent: None


def test_send_hands_the_attachments_over_and_clears_the_bar(panel, tmp_path):
    """
    The panel used to collect the attached paths and drop them on the floor.
    What it emits is what the run is given.
    """
    _never_call_the_real_model(panel)
    picture = tmp_path / "bracket.png"
    picture.write_bytes(b"a file; whether it decodes is the loop's problem")

    bundle = panel._worker_for(panel.chat_sessions.current_id())
    sent = []
    bundle.request_work.connect(lambda intent, files, log: sent.append((intent, files)))
    try:
        panel.chip_bar.add_file(str(picture))
        panel.user_input.setPlainText("Build this bracket")
        panel.on_send()
    finally:
        bundle.request_work.disconnect()
        panel._set_running(False)

    assert sent == [("Build this bracket", [str(picture)])]
    assert not panel.chip_bar.isVisible(), "the next Intent would reuse this one's files"


def test_a_clean_pass_still_clears_the_queue_line(panel):
    """The honest branch does not cost the quiet one: no `unmet` is still blank."""
    import types

    session_id = panel.chat_sessions.current_id()
    panel._worker_for(session_id)
    panel._queue_widget.now("Working…")

    panel.on_run_finished(types.SimpleNamespace(ok=True), panel.sessions.at(session_id)["log"])

    assert panel._queue_widget._now_label._full == ""


def test_an_unusable_attachment_stops_the_run_before_it_starts(panel, tmp_path, monkeypatch):
    """
    Told before the run, not after Plan already ignored it — and the chips stay
    on the bar, so the offending one can be taken off and the Intent sent
    again.
    """
    _mock_the_pills(monkeypatch)
    panel._refresh_models([{"id": "gpt-3.5-turbo", "display": "GPT-3.5", "provider": "OpenAI"}])
    drawing = tmp_path / "bracket.png"
    drawing.write_bytes(b"png")
    panel.chip_bar.add_file(str(drawing))

    bundle = panel._worker_for(panel.chat_sessions.current_id())
    sent = []
    listener = lambda intent, files, log: sent.append(intent)
    bundle.request_work.connect(listener)
    try:
        panel.user_input.setPlainText("Build this bracket")
        panel.on_send()
    finally:
        bundle.request_work.disconnect(listener)

    assert sent == [], "the run started with an attachment it could not use"
    assert panel.chip_bar.files() == [str(drawing)], "the user cannot take the chip off"
    assert panel.user_input.toPlainText() == "Build this bracket"
    assert "cannot accept images" in _said_in(panel), "the model's own limit went unmentioned"

    panel.chip_bar.remove_file(str(drawing))


def _on_the_clipboard(colour):
    """A 6×4 image of one colour, on the real clipboard. Returns it."""
    from conftest import QtGui

    image = QtGui.QImage(6, 4, QtGui.QImage.Format_RGB32)
    image.fill(QtGui.QColor(*colour))
    QT_APP.clipboard().setImage(image)
    return image


def test_pasting_writes_the_clipboard_image_and_not_a_blank(panel):
    """
    The pixels are the assertion. `QPixmap(mime.imageData())` is not a Qt5
    constructor — PySide2 takes the call and hands back a null pixmap, which
    `save()` writes as a blank and `file_attached` announces regardless. A
    test that only checked the file exists would pass on the blank.
    """
    from conftest import QtGui

    attached = []
    panel.upload_widget.file_attached.connect(attached.append)
    _on_the_clipboard((10, 200, 30))
    try:
        assert panel.upload_widget.handle_clipboard_paste() is True
    finally:
        panel.upload_widget.file_attached.disconnect(attached.append)

    assert len(attached) == 1, "the paste did not announce exactly one attachment"
    written = QtGui.QImage(attached[0])
    assert not written.isNull(), "the saved PNG is not an image at all"
    assert (written.width(), written.height()) == (6, 4)
    assert QtGui.QColor(written.pixel(3, 2)).getRgb()[:3] == (10, 200, 30), \
        "the file was written from a null pixmap, so the pasted picture is gone"

    panel.upload_widget.discard(attached[0])


def test_a_clipboard_that_promises_an_image_and_has_none_attaches_nothing(panel):
    """
    The shape of the PySide2 failure, reachable on any binding: the clipboard
    answers `hasImage()` yes and converts to nothing. What must not happen is
    a blank PNG landing on the chip bar as if it were the pasted picture.
    """
    from conftest import QtGui

    attached = []
    panel.upload_widget.file_attached.connect(attached.append)
    QT_APP.clipboard().setImage(QtGui.QImage())
    try:
        assert panel.upload_widget.handle_clipboard_paste() is False
    finally:
        panel.upload_widget.file_attached.disconnect(attached.append)

    assert attached == [], "a blank image was attached as though it were the paste"
    assert panel.upload_widget.temporary == [], "a blank PNG was left in the temp directory"


def test_a_paste_with_no_image_falls_through_to_the_default(panel):
    """Ctrl+V over text is still Ctrl+V over text."""
    QT_APP.clipboard().setText("40 mm across the flats")

    assert panel.upload_widget.handle_clipboard_paste() is False
    assert panel.upload_widget.temporary == [], "a text paste left a file behind"


def _a_pasted_image(panel, tmp_path, name):
    """What handle_clipboard_paste leaves behind, without a real clipboard."""
    path = str(tmp_path / name)
    open(path, "w").close()
    panel.upload_widget.temporary.append(path)
    panel.chip_bar.add_file(path)
    return path


def test_taking_the_chip_off_deletes_the_pasted_image(panel, tmp_path):
    pasted = _a_pasted_image(panel, tmp_path, "pasted.png")

    panel.chip_bar.remove_file(pasted)

    assert not os.path.exists(pasted), "a screenshot pasted and thought better of stays in /tmp"
    assert pasted not in panel.upload_widget.temporary


def test_taking_off_a_chip_the_user_chose_keeps_their_file(panel, tmp_path):
    theirs = tmp_path / "bracket.png"
    theirs.write_bytes(b"the user's own file, not ours to delete")
    panel.chip_bar.add_file(str(theirs))

    panel.chip_bar.remove_file(str(theirs))

    assert theirs.exists(), "removing a chip deleted a file the user attached from disk"


def test_closing_the_panel_takes_the_unsent_images_with_it(panel, tmp_path):
    from conftest import QtGui

    left_behind = _a_pasted_image(panel, tmp_path, "never-sent.png")

    panel.closeEvent(QtGui.QCloseEvent())

    assert not os.path.exists(left_behind), "closing with an image attached leaked it"


def _a_long_message(panel, times=8):
    from ui.chat_widgets import AssistantMessageWidget

    text = ("A wrapped sentence long enough that the panel has to break it over "
            "several lines before it fits. ") * times
    widget = panel.add_chat_widget(AssistantMessageWidget(text))
    for _ in range(6):
        QT_APP.processEvents()
    return widget


def test_a_wrapped_message_gets_the_height_its_text_needs(panel):
    """
    The transcript laid its messages out at `sizeHint`, which for a wrapped
    QLabel is a guess made at a width the panel does not have. Every message
    was then too short for its own text — the text was clipped and the space
    the column had already reserved for it was left blank, which is the
    "empty space, and the real text somewhere above" the user sees.
    """
    panel.resize(360, 700)
    QT_APP.processEvents()

    message = _a_long_message(panel)

    needed = message.heightForWidth(message.width())
    assert message.height() == needed, (
        "a message laid out %dpx tall needs %dpx for its wrapped text"
        % (message.height(), needed))


def test_a_short_message_gets_a_bubble_as_wide_as_its_words(panel):
    """
    A wrapped QLabel's `sizeHint` is a guess Qt makes at no width in
    particular, and it comes back smaller the more room the label is given.
    Under a Maximum size policy that guess is the ceiling the bubble can never
    pass, so four words wrapped into four lines in a dock wide enough for
    forty.
    """
    from conftest import QtGui

    from ui.chat_widgets import TextMessageWidget

    words = "зроби модель гайки m10"
    panel.resize(700, 700)
    QT_APP.processEvents()
    message = panel.add_chat_widget(TextMessageWidget(words))
    for _ in range(6):
        QT_APP.processEvents()

    natural = QtGui.QFontMetrics(message.text_label.font()).horizontalAdvance(words)
    assert message.text_label.width() >= natural, (
        "a bubble %dpx wide for words that need %dpx"
        % (message.text_label.width(), natural))


def test_a_long_title_on_a_folded_row_does_not_widen_the_panel(panel):
    """
    The row's title is a QPushButton, which neither wraps nor elides: a
    narration long enough to fold is exactly the narration long enough to push
    the transcript's column past the viewport, and the column's horizontal
    scrollbar is off, so what does not fit is unreachable rather than
    scrolled to -- the same defect, one widget along.
    """
    from ui.accordion_widget import AccordionWidget

    panel.resize(420, 700)
    panel.add_chat_widget(AccordionWidget(
        "Reading the sheet, the two circles are on the same centre line " * 6))
    for _ in range(6):
        QT_APP.processEvents()

    scroll = panel.chat_sessions.get_current_chat_history()
    assert scroll.widget().width() <= scroll.viewport().width(), (
        "the transcript is %dpx wide inside a %dpx viewport it cannot scroll"
        % (scroll.widget().width(), scroll.viewport().width()))


def test_the_transcript_stays_where_the_user_scrolled_it(panel):
    """
    Reading back through a run must survive the next event. Following the
    bottom unconditionally yanked the transcript away mid-sentence.
    """
    from conftest import QtWidgets

    for _ in range(6):
        _a_long_message(panel)
    scroll = panel.chat_sessions.get_current_chat_history()
    bar = scroll.verticalScrollBar()
    assert bar.maximum() > 0, "the transcript never grew past one screen"

    bar.triggerAction(QtWidgets.QAbstractSlider.SliderToMinimum)
    QT_APP.processEvents()

    _a_long_message(panel)

    assert bar.value() == 0, "adding a message dragged the transcript off where it was left"


def test_the_transcript_follows_the_run_when_it_is_at_the_bottom(panel):
    """The other half: not scrolled away, it keeps up with what arrives."""
    from conftest import QtWidgets

    scroll = panel.chat_sessions.get_current_chat_history()
    bar = scroll.verticalScrollBar()
    bar.triggerAction(QtWidgets.QAbstractSlider.SliderToMaximum)
    QT_APP.processEvents()

    _a_long_message(panel)
    scroll._scroll_anim.setCurrentTime(scroll._scroll_anim.duration())
    QT_APP.processEvents()

    assert bar.value() == bar.maximum(), "the transcript stopped following a live run"


def test_shutdown_does_not_wait_for_a_document_operation_nobody_will_take(sessions_are_the_tests_own):
    """
    The second way a worker blocks, and the one that hung FreeCAD again after
    the Plan Gate below was released.

    A Step in flight parks its thread inside `bridge.main_thread.on_main_thread`,
    waiting on a job only the GUI event loop can take. `_cleanup_thread` runs
    on that thread and waits on the worker instead of pumping it, so the job
    is never taken: the worker sits out `DISPATCH_TIMEOUT`, and what
    `_cleanup_thread` reaches for is `terminate()` on a thread in the middle
    of the Document.

    Same shape as the Plan Gate test: the shutdown runs in its own daemon
    thread and this one never pumps events, because a wait that does not
    return is what is being measured.
    """
    import threading

    import bridge.main_thread
    from conftest import QtCore
    from ui.daedalus_panel import DaedalusPanel

    built = DaedalusPanel()
    parked = threading.Event()

    def _dispatch_and_block():
        parked.set()
        try:
            bridge.main_thread.on_main_thread(lambda: "the Document")
        except Exception:
            pass

    blocked = QtCore.QTimer()
    blocked.moveToThread(built.thread)
    blocked.setSingleShot(True)
    blocked.timeout.connect(_dispatch_and_block, QtCore.Qt.DirectConnection)
    QtCore.QMetaObject.invokeMethod(blocked, "start", QtCore.Qt.QueuedConnection,
                                    QtCore.Q_ARG(int, 0))
    assert parked.wait(5), "the timer on built.thread never asked for the main thread"

    done = threading.Event()
    try:
        threading.Thread(target=lambda: (built._cleanup_thread(), done.set()),
                         daemon=True).start()

        assert done.wait(5), "shutdown is still waiting on a Document operation in flight"
        assert not built.thread.isRunning(), "the worker thread outlived the panel"
    finally:
        bridge.main_thread._closing.clear()


def test_shutdown_does_not_wait_for_a_question_nobody_will_answer(sessions_are_the_tests_own):
    """
    A run parked on the Plan Gate blocks its thread on an Event. `quit()` does
    not touch a thread that is inside a call, so waiting on it without first
    releasing the question is FreeCAD hanging on exit with no window left to
    answer from.

    In its own daemon thread: the defect under test is a wait that never
    returns, and a test that hangs says nothing to whoever runs the suite.
    """
    import threading

    from conftest import QtCore
    from ui.daedalus_panel import DaedalusPanel

    built = DaedalusPanel()
    parked = threading.Event()

    def _ask_and_block():
        parked.set()
        built.ask.about_the_plan(["a step"])

    blocked = QtCore.QTimer()
    blocked.moveToThread(built.thread)
    blocked.setSingleShot(True)
    blocked.timeout.connect(_ask_and_block, QtCore.Qt.DirectConnection)
    QtCore.QMetaObject.invokeMethod(blocked, "start", QtCore.Qt.QueuedConnection,
                                    QtCore.Q_ARG(int, 0))
    assert parked.wait(5), "the timer on built.thread never fired the Plan Gate"

    done = threading.Event()
    threading.Thread(target=lambda: (built._cleanup_thread(), done.set()),
                     daemon=True).start()

    assert done.wait(5), "shutdown is still waiting on a run parked at the Plan Gate"
    assert not built.thread.isRunning(), "the worker thread outlived the panel"


def _parked_on_the_main_thread(bundle):
    """A run of *bundle*'s, blocked inside `bridge.main_thread.on_main_thread`."""
    import threading

    import bridge.main_thread
    from conftest import QtCore

    parked = threading.Event()

    def _dispatch_and_block():
        parked.set()
        try:
            bridge.main_thread.on_main_thread(lambda: "the Document")
        except Exception:
            pass

    blocked = QtCore.QTimer()
    blocked.moveToThread(bundle.thread)
    blocked.setSingleShot(True)
    blocked.timeout.connect(_dispatch_and_block, QtCore.Qt.DirectConnection)
    QtCore.QMetaObject.invokeMethod(blocked, "start", QtCore.Qt.QueuedConnection,
                                    QtCore.Q_ARG(int, 0))
    assert parked.wait(5), "the run never asked for the main thread"
    return blocked


def test_a_document_closing_releases_a_run_parked_on_the_main_thread(sessions_are_the_tests_own):
    """
    FreeCAD deletes its Documents before it emits `aboutToQuit`, so the first
    teardown a closing FreeCAD runs is `slotDeletedDocument`, not
    `_cleanup_thread`. A run parked inside `bridge.main_thread.on_main_thread`
    waits on a job only the GUI event loop can take, and that thread is here,
    waiting on the run instead.

    Measured before the release was added: 3.0s in `slotDeletedDocument` and
    then SIGSEGV, from `terminate()` on a thread in the middle of the Document.

    In its own daemon thread: the defect is a wait that does not return, and a
    test that hangs says nothing to whoever runs the suite.
    """
    import threading
    import time

    import bridge.main_thread
    from ui.daedalus_panel import DaedalusPanel

    built = DaedalusPanel()
    bundle = built._worker_for(built.chat_sessions.current_id())

    class _Document:
        Uid = "the one the run was bound to"

    document = _Document()
    bundle.worker.document = document
    _parked_on_the_main_thread(bundle)
    bridge.main_thread._closing.clear()

    done = threading.Event()
    started = time.time()
    threading.Thread(target=lambda: (built.slotDeletedDocument(document), done.set()),
                     daemon=True).start()

    assert done.wait(5), "closing the Document is still waiting on a job in flight"
    took = time.time() - started
    assert took < 1, "closing the Document sat out the grace period: %.1fs" % took
    assert not bundle.thread.isRunning(), "the worker outlived the Document it was bound to"
    assert not bridge.main_thread._closing.is_set(), \
        "one Document closing refused the main thread to every other Session"
    assert bridge.main_thread._stopping == 0, "the teardown left the main thread refused"


def test_deleting_a_session_releases_a_run_parked_on_the_main_thread(
        sessions_are_the_tests_own, monkeypatch):
    """The same teardown by the other route: the user deleting a Session mid-run."""
    import threading
    import time

    import bridge.main_thread
    from conftest import QtWidgets
    from ui.daedalus_panel import DaedalusPanel

    built = DaedalusPanel()
    bundle = built._worker_for(built.chat_sessions.current_id())
    _parked_on_the_main_thread(bundle)
    bridge.main_thread._closing.clear()

    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes))

    session_id = built.chat_sessions.current_id()
    done = threading.Event()
    started = time.time()
    threading.Thread(target=lambda: (built.delete_session(session_id), done.set()),
                     daemon=True).start()

    assert done.wait(5), "deleting the Session is still waiting on a job in flight"
    took = time.time() - started
    assert took < 1, "deleting the Session sat out the grace period: %.1fs" % took
    assert not bundle.thread.isRunning(), "the deleted Session's worker is still running"
    assert not bridge.main_thread._closing.is_set(), \
        "one Session deleted refused the main thread to every other Session"
    assert bridge.main_thread._stopping == 0, "the teardown left the main thread refused"


@pytest.mark.parametrize("asker, args, snippet", [
    ("about_objects_we_do_not_own", (["Sketch"],), "did not create"),
])
def test_a_blocked_question_pages_freecads_notification_area(
        sessions_are_the_tests_own, monkeypatch, asker, args, snippet):
    """
    A person who walked away from a two-hour run is exactly who the
    question `AskTheUser` asks may be waiting on, so `_ask` raises
    FreeCAD's own Notification Area before it blocks -- something to come
    back to beyond a status line under a composer nobody is looking at.
    """
    import threading

    from conftest import QtCore
    from ui.daedalus_panel import DaedalusPanel

    calls = []
    monkeypatch.setattr("bridge.logger.notify_user", lambda msg: calls.append(msg))

    built = DaedalusPanel()
    parked = threading.Event()

    def _ask_and_block():
        parked.set()
        getattr(built.ask, asker)(*args)

    blocked = QtCore.QTimer()
    blocked.moveToThread(built.thread)
    blocked.setSingleShot(True)
    blocked.timeout.connect(_ask_and_block, QtCore.Qt.DirectConnection)
    QtCore.QMetaObject.invokeMethod(blocked, "start", QtCore.Qt.QueuedConnection,
                                    QtCore.Q_ARG(int, 0))
    assert parked.wait(5), "the timer on built.thread never fired %s" % asker

    for _ in range(50):
        if calls:
            break
        QtCore.QThread.msleep(20)
    built.ask.abandon()
    built._cleanup_thread()

    assert len(calls) == 1
    assert snippet in calls[0]


def test_the_panel_docks_on_the_main_window_once(sessions_are_the_tests_own, monkeypatch):
    """
    Like the Tasks panel: it is a fixture of the window, so it is there on
    whatever workbench the user is working in. Installing twice — two
    workbench switches — must not build a second panel.
    """
    from conftest import QtWidgets
    from ui import daedalus_dock

    window = QtWidgets.QMainWindow()
    monkeypatch.setattr(daedalus_dock, "_panel", None)
    try:
        panel = daedalus_dock.install(window)
        again = daedalus_dock.install(window)

        assert again is panel, "a second install built a second panel"
        assert panel.parent() is window, "the panel is not docked on the main window"
        assert window.findChildren(type(panel)) == [panel]
    finally:
        panel._cleanup_thread()
        window.deleteLater()


def test_the_run_lock_and_the_dock_agree_on_the_chats_name(panel):
    """
    `lock_gui` knows the chat by its object name, and the panel sets it. Spelt
    twice because `core` may not be imported at panel-import time, so nothing
    but this holds the two spellings together.
    """
    from core.freecad_apply import CHAT_DOCK

    assert panel.objectName() == CHAT_DOCK


def test_no_workbench_of_ours_exists_to_take_the_chat_away():
    """
    The dock is a fixture of the main window, not of a workbench —
    there is no `Deactivated` hook left anywhere that could hide it.
    Read from the source: `InitGui.py` never calls `Gui.addWorkbench`.
    """
    import ast
    import os

    from ui import daedalus_dock

    mod_dir = os.path.dirname(os.path.dirname(os.path.abspath(daedalus_dock.__file__)))
    assert not os.path.exists(os.path.join(mod_dir, "DaedalusWorkbench.py"))

    root = os.path.dirname(mod_dir)
    path = os.path.join(root, "InitGui.py")
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), path)
    workbench_calls = [node for node in ast.walk(tree)
                        if isinstance(node, ast.Attribute) and node.attr == "addWorkbench"]
    assert workbench_calls == [], "InitGui.py still registers a workbench"


def _a_row(name="Bracket with two bores", document="Parts.FCStd", created_at=0.0):
    from ui.history_popup import HistoryItem

    return HistoryItem(0, name, created_at, document)


def test_the_picker_drops_from_the_top_of_the_dock_at_its_width(panel):
    """It used to open in the middle of the screen, 280px wide, detached."""
    from conftest import QtCore

    contents = panel.widget()
    for width in (360, 520):
        panel.resize(width, 700)
        QT_APP.processEvents()
        panel.history_popup.close()
        panel.toggle_history()
        QT_APP.processEvents()

        assert panel.history_popup.width() == contents.width()
        assert panel.history_popup.pos() == contents.mapToGlobal(QtCore.QPoint(0, 0))
    panel.history_popup.close()


def test_opening_the_picker_never_widens_the_dock(panel):
    """The picker is a window of its own; the dock must not pay for its width."""
    contents = panel.widget()
    narrow = contents.minimumSizeHint().width()

    panel.toggle_history()
    QT_APP.processEvents()
    panel.history_popup.close()

    assert contents.minimumSizeHint().width() == narrow


def test_a_session_is_one_line_with_the_time_beside_the_name(panel):
    """Two stacked lines in a 48px row is what made the list unscannable."""
    row = _a_row()

    assert row.height() <= 32
    assert row.title_label.geometry().center().y() == \
        row.time_label.geometry().center().y()
    assert row.time_label.graphicsEffect().opacity() < 1.0


def test_a_narrow_row_gives_up_the_time_before_the_name(panel):
    """The list is scanned by name, so the name is what survives."""
    row = _a_row(name="A bracket with two bores and a chamfered edge")
    row.show()
    row.resize(400, row.height())
    QT_APP.processEvents()
    assert row.time_label.isVisible()

    row.resize(120, row.height())
    QT_APP.processEvents()

    assert not row.time_label.isVisible()
    assert row.del_btn.width() == 24, "the delete button is still on the row"


def test_the_document_a_session_worked_on_is_in_the_row_tooltip(panel):
    assert _a_row(document="Parts.FCStd").toolTip() == "Parts.FCStd"


def test_the_row_name_and_time_take_their_type_from_the_design_system(panel):
    """13px/11px were not on the four-pair scale added earlier."""
    from ui.typography import CAPTION, LABEL, css

    row = _a_row()

    assert css(LABEL) in row.title_label.styleSheet()
    assert css(CAPTION) in row.time_label.styleSheet()


def _icon_drawn(button, size=14):
    """The fully-opaque colours the button's icon actually paints."""
    from conftest import QtGui

    image = button.icon().pixmap(size, size).toImage()
    return [QtGui.QColor(image.pixelColor(x, y))
            for y in range(image.height())
            for x in range(image.width())
            if QtGui.QColor(image.pixelColor(x, y)).alpha() == 255]


def test_the_delete_button_draws_a_trash_icon_at_all(panel):
    """`QIcon(path)` on an SVG rasterises to a null pixmap wherever Qt's SVG
    image plugin is not loaded, and the button then draws nothing."""
    row = _a_row()
    row.show()
    QT_APP.processEvents()

    assert not row.del_btn.icon().isNull(), "the delete button has no icon"
    assert _icon_drawn(row.del_btn), "the icon rasterised to nothing — a null pixmap"


def test_the_delete_icon_is_tinted_to_the_button_like_every_other_icon(panel):
    """
    The row's icon was the one call site not going through `UIHelpers.get_icon`,
    so it painted the SVG's own hardcoded white — invisible under a light
    FreeCAD theme, and a colour of the addon's own, which the theme rules forbid.

    A theme is one global stylesheet, which is what the colour has to arrive
    through — that route, not a QPalette call.
    """
    row = _a_row()
    for wanted in ("#c8321e", "#1e64c8"):
        row.del_btn.setStyleSheet(
            "QPushButton#DeleteButton { background: transparent; border: none; color: %s; }" % wanted)
        row.hide()
        row.show()
        QT_APP.processEvents()

        painted = {colour.name() for colour in _icon_drawn(row.del_btn)}
        assert painted == {wanted}, \
            f"the trash icon painted {painted or 'nothing'} where the button said {wanted}"


def test_a_row_never_changes_height_under_the_cursor(panel):
    from conftest import QtCore

    row = _a_row()
    row.show()
    QT_APP.processEvents()
    resting = row.height()

    QT_APP.sendEvent(row, QtCore.QEvent(QtCore.QEvent.Enter))
    QT_APP.processEvents()

    assert row.height() == resting


def _rows_in_the_picker(panel):
    """The names the popup is actually showing, in order."""
    from ui.history_popup import HistoryItem

    panel.toggle_history()
    try:
        rows = [panel.history_popup.list_layout.itemAt(i).widget()
                for i in range(panel.history_popup.list_layout.count())]
        return [(row.session_id, row.title_label.text())
                for row in rows if isinstance(row, HistoryItem)]
    finally:
        panel.history_popup.close()


def test_a_session_with_no_intent_is_not_in_the_picker_either(panel):
    """
    QA against FreeCAD 1.1.3 found the invariant enforced in the durable session_id
    and not in the picker: `toggle_history` built the popup from every Session
    in the stack, so a fresh dock showed one `New Chat` row against an session_id
    holding none of it.
    """
    session_id, _ = panel.chat_sessions.add_new_chat("New Chat")

    assert "New Chat" not in [name for _, name in _rows_in_the_picker(panel)]

    panel.sessions.begin(session_id, "Two-bore bracket")

    assert "Two-bore bracket" in [name for _, name in _rows_in_the_picker(panel)]


def test_a_promptless_launch_adds_no_row_to_the_picker(panel):
    """Three launches where nobody typed anything, as the QA run did."""
    session_id, _ = panel.chat_sessions.add_new_chat("Real work")
    panel.sessions.begin(session_id, "Two-bore bracket")
    before = _rows_in_the_picker(panel)

    for _ in range(3):
        panel.chat_sessions.add_new_chat("New Chat")

    assert [name for _, name in _rows_in_the_picker(panel)] == \
        [name for _, name in before]


def test_a_picker_row_names_the_session_it_shows_not_its_own_position(panel, monkeypatch):
    """
    Naming a third Session prunes the first, which moves everything after it.
    The row carries the Session's id, so choosing it opens that Session and
    not the one that now sits where it used to.
    """
    from core import sessions as sessions_module

    monkeypatch.setattr(sessions_module, "KEEP", 2)
    monkeypatch.setattr(panel, "_ask_for_a_title", lambda *_: None)
    ids = {}
    for name in ("first", "second", "third"):
        session_id, _ = panel.chat_sessions.add_new_chat("New Chat")
        panel._name_session(session_id, name)
        ids[name] = session_id

    rows = dict(_rows_in_the_picker(panel))
    assert rows == {ids["second"]: "second", ids["third"]: "third"}

    panel.switch_session(ids["second"])

    assert panel.chat_sessions.current_id() == ids["second"]
    assert panel.title_label.text() == "second"
    assert panel.chat_sessions.stack.count() == panel.sessions.count()


def test_a_title_lands_on_the_session_that_asked_for_it(panel):
    """
    The call outlives every other Session: one deleted while a title was in
    flight must not make it land on a neighbour.
    """
    doomed, _ = panel.chat_sessions.add_new_chat("New Chat")
    panel.sessions.begin(doomed, "the one that gets deleted")
    asker, _ = panel.chat_sessions.add_new_chat("New Chat")
    panel.sessions.begin(asker, "the one that asked")

    panel.sessions.remove(doomed)
    panel.chat_sessions.drop_page(doomed)
    panel._title_arrived(asker, "Two-bore bracket")

    assert panel.sessions.at(asker)["name"] == "Two-bore bracket"
    assert panel.title_label.text() == "Two-bore bracket"


def test_a_launch_opens_an_empty_session_and_keeps_the_earlier_ones(sessions_are_the_tests_own):
    """
    The panel used to reopen the last Session with its transcript.
    """
    from ui.daedalus_panel import DaedalusPanel
    from bridge import settings_manager

    first = DaedalusPanel()
    try:
        first.sessions.begin(first.chat_sessions.current_id(), "yesterday")
    finally:
        first._cleanup_thread()

    relaunched = DaedalusPanel()
    try:
        live = relaunched.chat_sessions.current_id()
        assert not relaunched.sessions.at(live)["indexed"]
        assert relaunched.sessions.count() >= 2, "the earlier Session was dropped"
        names = [s["name"] for s in settings_manager.instance().get_sessions()]
        assert "yesterday" in names
    finally:
        relaunched._cleanup_thread()


def test_a_first_intent_names_the_session_from_what_was_typed(panel, monkeypatch):
    """
    A readable name is there from the start — never "Chat 1", never empty.

    An attachment the model cannot read turns the Intent away before it names
    anything, which is the behaviour — not this test's subject.
    """
    asked = []
    monkeypatch.setattr(panel, "_ask_for_a_title", lambda session_id, intent: asked.append(intent))
    panel.chip_bar.get_and_clear()
    panel.new_chat()
    session_id = panel.chat_sessions.current_id()
    _never_call_the_real_model(panel)

    bundle = panel._worker_for(panel.chat_sessions.current_id())
    bundle.request_work.connect(lambda *_: None)
    panel.user_input.setPlainText(
        "Model a spur gear with 24 teeth, module 2, 10mm thick, and a 12mm bore")
    try:
        panel.on_send()
    finally:
        bundle.request_work.disconnect()
        panel._set_running(False)

    name = panel.sessions.at(session_id)["name"]
    assert name.startswith("Model a spur gear")
    assert panel.title_label.text() == name
    assert asked, "the generated title was never asked for"


def _widgets_in(panel):
    """What the active Session's chat area is currently showing, in order."""
    area = panel.chat_sessions.get_current_chat_history()
    layout = area.widget().layout()
    return [layout.itemAt(i).widget() for i in range(layout.count())
            if layout.itemAt(i).widget() is not None]


def _now_on(panel):
    """What the pinned Queue says is happening, in full rather than elided."""
    return panel._queue_widget._now_label._full


def _said_in(panel):
    """Every label the Session's chat area is showing, as one string."""
    return _said_by(_widgets_in(panel))


def _said_by(widgets):
    """
    The same, over the widgets one test put there.

    The panel is shared, so a test that measures what the chat carries measures
    its own widgets rather than everything the module has rendered into it.
    """
    from conftest import QtWidgets

    return " ".join(label.text()
                    for widget in widgets
                    for label in widget.findChildren(QtWidgets.QLabel))


def _a_transcript(path):
    from core.events import APPLIED, COMPLETED, QUESTION, THOUGHT, EventStream

    stream = EventStream()
    stream.log_to(path)
    stream.emit(THOUGHT, phase="plan", text="a box, then a fillet")
    stream.emit(QUESTION, text="How tall?")
    stream.emit(APPLIED, step=1, label="Box", created=["Box"])
    stream.emit(COMPLETED, summary="Looks like a box.")


def _a_session_opened_from(panel, tmp_path, name, retired=False):
    log = str(tmp_path / (name + ".jsonl"))
    _a_transcript(log)
    if retired:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write('{"kind": "plan", "at": 1.0, "data": {"steps": ["Make a box"]}}\n')
    session_id, _ = panel.chat_sessions.add_new_chat(name)
    panel.sessions.at(session_id).update({"log": log, "restored": False})
    panel.switch_session(session_id)


def test_a_retired_kind_in_a_replayed_log_draws_nothing_and_is_logged_once(
        panel, tmp_path, monkeypatch):
    from bridge.logger import logger

    _a_session_opened_from(panel, tmp_path, "plain")
    plain = len(_widgets_in(panel))
    logged = []
    monkeypatch.setattr(logger, "warning", logged.append)

    _a_session_opened_from(panel, tmp_path, "retired", retired=True)

    assert len(_widgets_in(panel)) == plain
    assert "unrecognized" not in _said_in(panel)
    assert len(logged) == 1 and "'plan'" in logged[0]


def test_a_session_comes_back_with_its_whole_transcript(panel, tmp_path):
    """
    Restoring replays the log through the switch that rendered it live, so a
    restored Session cannot drift from a live one.
    """
    from ui.accordion_widget import AccordionWidget
    log = str(tmp_path / "yesterday.jsonl")
    _a_transcript(log)
    session_id, _ = panel.chat_sessions.add_new_chat("yesterday")
    panel.sessions.at(session_id)["log"] = log
    panel.sessions.at(session_id)["restored"] = False

    panel.switch_session(session_id)

    kinds = [type(w).__name__ for w in _widgets_in(panel)]
    assert any(isinstance(w, AccordionWidget) for w in _widgets_in(panel)), kinds
    said = _said_in(panel)
    assert "Applied" in said and "Box" in said
    assert "Done" in said


def _a_cost_transcript(path):
    """Two calls, Design then Plan: the total after both is their sum."""
    from core.events import COST, EventStream, INTENT

    stream = EventStream()
    stream.log_to(path)
    stream.emit(INTENT, text="a box")
    stream.emit(COST, phase="design", usd=0.04, model="sonnet")
    stream.emit(COST, phase="plan", usd=0.09, model="sonnet")


def test_a_new_chat_shows_the_zero_state(panel):
    panel.new_chat()

    assert panel.usage_label.text() == "$0.00  0:00"


def test_cost_events_accumulate_into_one_running_total(panel):
    from core.events import COST, Event

    panel.new_chat()

    panel.on_event(Event(COST, {"phase": "design", "usd": 0.04, "model": "sonnet", "seconds": 1.0}))
    assert panel.usage_label.text().startswith("$0.04")

    panel.on_event(Event(COST, {"phase": "plan", "usd": 0.07, "model": "sonnet", "seconds": 1.0}))
    assert panel.usage_label.text().startswith("$0.11")


def test_a_call_under_a_cent_still_shows_what_it_cost(panel):
    from core.events import COST, Event

    panel.new_chat()

    panel.on_event(Event(COST, {"phase": "design", "usd": 0.0049, "model": "sonnet", "seconds": 1.0}))

    assert panel.usage_label.text().startswith("$0.0049")


def test_a_new_intent_zeroes_the_total_mid_session(panel):
    """
    A follow-up Intent in the same open chat starts a second run, and the
    total is the run's, not the Session's (Solution Notes: "cumulative cost
    for the entire run from Intent to Review verdict") -- the same boundary
    `_update_queue` already resets `_queue_widget` on.
    """
    from core.events import COST, INTENT, Event

    panel.new_chat()
    panel.on_event(Event(COST, {"phase": "design", "usd": 0.04, "model": "sonnet", "seconds": 1.0}))
    assert panel.usage_label.text().startswith("$0.04")

    panel.on_event(Event(INTENT, {"text": "a second box"}))

    assert panel.usage_label.text().startswith("$0.00")


def test_a_restored_session_shows_its_final_total(panel, tmp_path):
    log = str(tmp_path / "cost-run.jsonl")
    _a_cost_transcript(log)
    session_id, _ = panel.chat_sessions.add_new_chat("cost-run")
    panel.sessions.at(session_id)["log"] = log
    panel.sessions.at(session_id)["restored"] = False

    panel.switch_session(session_id)

    assert panel.usage_label.text().startswith("$0.13")


def test_a_session_recorded_before_the_step_render_went_away_still_opens(panel, tmp_path):
    """
    An old transcript carries `render` events, and this build has no such kind
    any more. The renderer skips it the way it skips any kind it does
    not know: a Session is not refused over an event that has been retired.
    """
    log = str(tmp_path / "before.jsonl")
    _a_transcript(log)
    with open(log, "a", encoding="utf-8") as handle:
        handle.write('{"kind": "render", "at": 1.0, '
                     '"data": {"path": "/tmp/daedalus_step_1.png", "step": 1}}\n')
    session_id, _ = panel.chat_sessions.add_new_chat("before")
    panel.sessions.at(session_id).update({"log": log, "restored": False})

    panel.switch_session(session_id)

    assert "Done" in _said_in(panel), "the transcript did not replay"


def test_a_session_is_replayed_once_however_often_it_is_opened(panel, tmp_path):
    log = str(tmp_path / "twice.jsonl")
    _a_transcript(log)
    session_id, _ = panel.chat_sessions.add_new_chat("twice")
    panel.sessions.at(session_id).update({"log": log, "restored": False})

    panel.switch_session(session_id)
    first = len(_widgets_in(panel))
    panel.switch_session(session_id)

    assert len(_widgets_in(panel)) == first, "the transcript was rendered twice"


def _a_long_transcript(path, steps):
    from core.events import APPLIED, EventStream

    stream = EventStream()
    stream.log_to(path)
    for step in range(steps):
        stream.emit(APPLIED, step=step, label="Box%d" % step, created=["Box%d" % step])


def _spin(times=50):
    """Give the event loop the turns a chunked replay asks for."""
    from conftest import QT_APP

    for _ in range(times):
        QT_APP.processEvents()


def test_a_long_transcript_is_replayed_a_chunk_at_a_time(panel, tmp_path, monkeypatch):
    """
    Opening a Session with hundreds of Steps used to replay them all in one
    pass on the GUI thread, and the panel was frozen for the duration.
    """
    from ui import daedalus_panel

    log = str(tmp_path / "long.jsonl")
    _a_long_transcript(log, 120)
    session_id, _ = panel.chat_sessions.add_new_chat("long")
    panel.sessions.at(session_id).update({"log": log, "restored": False})

    replayed = []
    real = panel.on_event
    monkeypatch.setattr(panel, "on_event", lambda event: replayed.append(event) or real(event))

    panel.switch_session(session_id)

    assert len(replayed) == daedalus_panel.RESTORE_CHUNK, \
        "the whole transcript was replayed before the event loop got a turn"

    _spin()

    assert len(replayed) == 120, "the rest of the transcript never arrived"


def test_a_short_transcript_still_lands_in_one_go(panel, tmp_path):
    """Chunking must not make the common case wait for an event-loop turn."""
    log = str(tmp_path / "short.jsonl")
    _a_transcript(log)
    session_id, _ = panel.chat_sessions.add_new_chat("short")
    panel.sessions.at(session_id).update({"log": log, "restored": False})

    panel.switch_session(session_id)

    assert "Done" in _said_in(panel)


def test_switching_away_mid_replay_abandons_the_rest(panel, tmp_path, monkeypatch):
    """The remaining chunks must not render into whichever Session is open now."""
    log = str(tmp_path / "abandoned.jsonl")
    _a_long_transcript(log, 120)
    session_id, _ = panel.chat_sessions.add_new_chat("abandoned")
    panel.sessions.at(session_id).update({"log": log, "restored": False})
    elsewhere, _ = panel.chat_sessions.add_new_chat("elsewhere")

    replayed = []
    real = panel.on_event
    monkeypatch.setattr(panel, "on_event", lambda event: replayed.append(event) or real(event))

    panel.switch_session(session_id)
    panel.switch_session(elsewhere)
    _spin()

    from ui import daedalus_panel
    assert len(replayed) == daedalus_panel.RESTORE_CHUNK
    assert _widgets_in(panel) == [], "another Session's transcript landed here"


def test_a_truncated_transcript_shows_what_survived_and_says_so(panel, tmp_path):
    log = str(tmp_path / "crashed.jsonl")
    _a_transcript(log)
    with open(log, "a", encoding="utf-8") as handle:
        handle.write('{"kind": "blocked", "at": 1.0, "da')
    session_id, _ = panel.chat_sessions.add_new_chat("crashed")
    panel.sessions.at(session_id).update({"log": log, "restored": False})

    panel.switch_session(session_id)

    said = _said_in(panel)
    assert "Applied" in said, "the surviving transcript was thrown away"
    assert "ends mid-event" in said


def test_an_event_kind_the_panel_does_not_know_is_skipped_not_fatal(panel, tmp_path, monkeypatch):
    from bridge.logger import logger

    log = str(tmp_path / "from_the_future.jsonl")
    _a_transcript(log)
    with open(log, "a", encoding="utf-8") as handle:
        handle.write('{"kind": "almost_applied", "at": 1.0, "data": {"step": 1}}\n')
    session_id, _ = panel.chat_sessions.add_new_chat("future")
    panel.sessions.at(session_id).update({"log": log, "restored": False})
    logged = []
    monkeypatch.setattr(logger, "warning", logged.append)

    panel.switch_session(session_id)

    said = _said_in(panel)
    assert "Done" in said
    assert "unrecognized" not in said
    assert len(logged) == 1 and "almost_applied" in logged[0]


def test_a_rename_persists_and_reaches_the_header(panel):
    """
    Only a Session that has had an Intent is in the session_id at all, and the
    session_id no longer lines up positionally with the open Sessions.
    """
    from bridge import settings_manager

    session_id, _ = panel.chat_sessions.add_new_chat("New Chat")
    panel.chat_sessions.switch_to(session_id)
    panel.sessions.begin(session_id, "a first intent")
    log = panel.sessions.at(session_id)["log"]

    panel.sessions.rename(session_id, "bracket revision")
    panel.update_header_title(session_id)

    assert panel.title_label.text() == "bracket revision"
    stored = {s["log"]: s["name"] for s in settings_manager.instance().get_sessions()}
    assert stored[log] == "bracket revision"


def test_deleting_a_session_takes_its_transcript_with_it(panel, tmp_path, monkeypatch):
    from bridge import settings_manager
    from conftest import QtWidgets

    log = str(tmp_path / "doomed.jsonl")
    _a_transcript(log)
    session_id, _ = panel.chat_sessions.add_new_chat("doomed")
    panel.sessions.at(session_id)["log"] = log
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes))

    panel.delete_session(session_id)

    assert not os.path.exists(log), "delete hid the Session and kept the transcript"
    assert "doomed" not in [s["name"] for s in settings_manager.instance().get_sessions()]


def test_nothing_grabs_the_left_button_off_the_transcript():
    """
    A QScroller on the viewport pans the column with the left button, which
    takes the press before a message label can select its text and drags the
    column sideways until the Gate's Build button is off the panel.
    """
    from conftest import QtWidgets
    from ui import multi_tab_manager

    manager = multi_tab_manager.ChatSessionManager()
    _, area = manager.add_new_chat("scroller")

    assert not QtWidgets.QScroller.hasScroller(area.viewport())


def test_pruning_takes_the_chat_area_with_the_session(panel, monkeypatch):
    """A page left behind would show the wrong Session's transcript."""
    from core import sessions as sessions_module

    monkeypatch.setattr(sessions_module, "KEEP", 3)
    monkeypatch.setattr(panel, "_ask_for_a_title", lambda *_: None)
    for number in range(5):
        session_id, _ = panel.chat_sessions.add_new_chat("New Chat")
        panel._name_session(session_id, "Chat %d" % number)

    assert panel.chat_sessions.stack.count() == panel.sessions.count(), \
        "a page outlived the Session it was showing"
    assert len(panel.sessions.indexed()) == 3
    assert panel.sessions.at(panel.chat_sessions.current_id())["name"] == "Chat 4"


def test_a_restored_session_owns_nothing(panel, tmp_path):
    """
    Each Session's Owned Objects live on its own worker, not one shared one
    not one shared one. Reopening yesterday's Session must not grant the assistant
    rights over objects another Session owns, and switching away from a
    Session that does own something must not cost it that ownership either —
    it is not reset on every switch any more, because the pipeline switching
    away from is no longer the pipeline switching in.
    """
    live = panel.chat_sessions.current_id()
    panel.worker._get_sdk_client()
    panel.worker.pipeline.owned = {"BoxFromTheLiveSession"}

    log = str(tmp_path / "old.jsonl")
    _a_transcript(log)
    session_id, _ = panel.chat_sessions.add_new_chat("old")
    panel.sessions.at(session_id).update({"log": log, "restored": False})

    panel.switch_session(session_id)
    panel.worker._get_sdk_client()
    assert panel.worker.pipeline.owned == set(), \
        "the next Step would apply against another Session's Owned Objects"

    panel.switch_session(live)
    assert panel.worker.pipeline.owned == {"BoxFromTheLiveSession"}, \
        "switching away and back cost the live Session its own Owned Objects"


def test_a_new_session_owns_nothing(panel):
    panel.worker._get_sdk_client()
    panel.worker.pipeline.owned = {"BoxFromAnotherSession"}

    panel.new_chat()

    panel.worker._get_sdk_client()
    assert panel.worker.pipeline.owned == set()


def test_returning_to_a_session_does_not_keep_another_ones_widgets(panel, tmp_path):
    """
    Restoring is skipped the second time a Session is opened, but the render
    state still has to let go of the Session being left.
    """
    log = str(tmp_path / "first.jsonl")
    _a_transcript(log)
    first, _ = panel.chat_sessions.add_new_chat("first")
    panel.sessions.at(first).update({"log": log, "restored": False})
    panel.new_chat()
    second = panel.chat_sessions.current_id()

    panel.switch_session(first)
    panel.switch_session(second)

    assert panel._empty_state is None or \
        panel._empty_state.parent() is not None, "empty state points at a dead widget"


def test_switching_mid_run_moves_the_panel_without_touching_the_run(panel):
    """
    A run narrates into the Session it started from, but the Session it
    started from is no longer the only one the panel can show: each
    Session runs on its own worker, so leaving one running and looking at
    another is switching, not interrupting.
    """
    first, _ = panel.chat_sessions.add_new_chat("running")
    second, _ = panel.chat_sessions.add_new_chat("elsewhere")
    panel.chat_sessions.switch_to(first)
    first_bundle = panel._worker_for(panel.sessions.at(first)["log"])
    first_bundle.running = True
    panel._set_running(True)

    panel.switch_session(second)

    assert panel.chat_sessions.current_id() == second, \
        "a run in another Session refused a switch that no longer touches it"
    assert first_bundle.running, "switching away stopped a run it did not start"
    first_bundle.running = False


def test_activating_a_documents_own_session_switches_the_panel_to_it(panel):
    """
    Bringing a Document's MDI tab forward in FreeCAD moves the panel to
    whichever Session named that Document, without the user touching the
    picker (an Acceptance Criterion).
    """
    import FreeCAD

    doc = FreeCAD.newDocument("FCC181PanelFollowsMDI")
    try:
        elsewhere, _ = panel.chat_sessions.add_new_chat("elsewhere")
        named, _ = panel.chat_sessions.add_new_chat("named")
        panel.sessions.name_document(
            named, doc.Name, doc.Uid)
        panel.chat_sessions.switch_to(elsewhere)

        panel.slotActivateDocument(doc)

        assert panel.chat_sessions.current_id() == named, \
            "activating the Document did not bring its own Session forward"
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_a_new_unsaved_document_does_not_reopen_an_older_unsaved_ones_chat(panel):
    """
    Every unsaved Document in FreeCAD is called "Unnamed", so a name is not
    an identity: the panel must stay where it is rather than open the chat of
    some older file the user is not looking at.
    """
    import FreeCAD

    doc = FreeCAD.newDocument("FCC169FreshDocument")
    try:
        elsewhere, _ = panel.chat_sessions.add_new_chat("elsewhere")
        older, _ = panel.chat_sessions.add_new_chat("older")
        panel.sessions.name_document(
            older,
            doc.Name, "a-document-that-is-long-gone")
        panel.chat_sessions.switch_to(elsewhere)

        panel.slotActivateDocument(doc)

        assert panel.chat_sessions.current_id() == elsewhere
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_activating_an_unclaimed_document_leaves_the_panel_alone(panel):
    """A Document nothing has been sent to yet has no Session to switch to."""
    import FreeCAD

    doc = FreeCAD.newDocument("FCC181PanelIgnoresUnclaimedDoc")
    try:
        session_id, _ = panel.chat_sessions.add_new_chat("current")
        panel.chat_sessions.switch_to(session_id)

        panel.slotActivateDocument(doc)

        assert panel.chat_sessions.current_id() == session_id
    finally:
        FreeCAD.closeDocument(doc.Name)


def _button(card, text):
    from conftest import QtWidgets

    for button in card.findChildren(QtWidgets.QPushButton):
        if text in button.text():
            return button
    raise AssertionError("no %r button on the card: %s"
                         % (text, [b.text() for b in card.findChildren(QtWidgets.QPushButton)]))


def test_the_question_reaches_the_transcript(panel):
    from core.events import Event, QUESTION

    panel.on_event(Event(QUESTION, {"text": "How many teeth, and what module?"}))
    QT_APP.processEvents()

    assert "How many teeth, and what module?" in _said_in(panel)


def test_the_composers_controls_stay_narrow_under_freecads_own_theme(panel):
    """
    FreeCAD's stylesheet is `QPushButton { min-width: 80px; }`, and an id rule
    only overrides the properties it names. Silent about `min-width`, the
    attach button was 80px wide where it asked for 20 and each picker was 88,
    so `+ sonnet medium` was three quarters of a docked panel and Send sat on
    the edge of it. `setFixedSize` did not save the attach button: a
    stylesheet minimum feeds `minimumSizeHint`, which is what the layout asks.

    Measured against the theme's own rule rather than a bare panel, because a
    bare panel is exactly where the defect does not show. The whole strip, not
    each control: what the user loses is the room the three of them take
    together.
    """
    from ui.design_tokens import CONTROL, ICON, SPACE_XS

    panel.setStyleSheet(panel.styleSheet() + "\nQPushButton { min-width: 80px; }")
    try:
        panel._set_current_model("x", "sonnet")
        QT_APP.processEvents()

        attach = panel.upload_widget.upload_btn
        assert attach.width() == ICON + SPACE_XS, "the theme widened the attach button"
        assert panel.send_button.width() == CONTROL

        strip = (panel.model_button.geometry().right()
                 - panel.upload_widget.geometry().left())
        assert strip < panel.width() // 2, \
            "the attach button and the model pill are half the panel between them"
    finally:
        panel.setStyleSheet(panel.styleSheet().replace(
            "\nQPushButton { min-width: 80px; }", ""))
        QT_APP.processEvents()


def test_the_composer_says_when_the_keyboard_is_in_it(panel):
    """
    A field with no focus mark is a field the user cannot tell they are typing
    into. The mark is the composer card's own hairline repainted in the accent
    -- Qt has no `:focus-within`, so the card carries a property the field
    that actually takes the keyboard sets.

    The window is activated first: Qt delivers no FocusIn to a widget whose
    window is not the active one, and the popups earlier tests open leave it
    somewhere else.
    """
    from ui.daedalus_panel import _build_qss

    panel.activateWindow()
    panel.raise_()
    panel.user_input.clearFocus()
    QT_APP.processEvents()
    assert panel._input_card.property("focused") == "false"

    panel.user_input.setFocus()
    QT_APP.processEvents()
    assert panel._input_card.property("focused") == "true"

    panel.user_input.clearFocus()
    QT_APP.processEvents()
    assert panel._input_card.property("focused") == "false"

    assert 'QFrame#InputContainer[focused="true"]' in _build_qss(), \
        "nothing in the stylesheet draws the ring the property asks for"


def test_a_model_name_too_long_for_the_strip_is_elided_not_shown_whole(panel):
    """
    The pill sizes itself to its own text, and `claude-sonnet-4-5-20250929` is
    what the OpenAI-shaped routes hand back where a display name is missing.
    At that length the two pickers are the whole composer strip and Send is
    pushed off the edge of a docked panel. The name in full moves to the
    tooltip, where nothing is measured by it.
    """
    from ui.daedalus_panel import PICKER_SHARE

    panel._set_current_model("x", "claude-sonnet-4-5-20250929-thinking-preview")
    QT_APP.processEvents()

    shown = panel.model_button.text()
    assert shown != "claude-sonnet-4-5-20250929-thinking-preview"
    assert shown.endswith("\u2026"), shown
    assert panel.model_button.width() <= int(panel.width() * PICKER_SHARE)
    assert "claude-sonnet-4-5-20250929-thinking-preview" in panel.model_button.toolTip()

    panel._set_current_model("x", "GPT-4o")
    QT_APP.processEvents()
    assert panel.model_button.text() == "GPT-4o", "a short name is elided too"


def test_stop_does_not_build_a_fresh_worker_for_an_idle_session(panel):
    """
    `on_stop`/`on_abort` used to reach the worker through the
    `worker` property, which is `_worker_for` -- and `_worker_for` builds and
    registers a fresh idle bundle for a Session with nothing running. Stopping
    a Session that never sent an Intent must not silently manufacture one.
    """
    panel.new_chat()
    session_id = panel.chat_sessions.current_id()
    assert session_id not in panel._workers

    panel.on_stop()

    assert session_id not in panel._workers


def test_abort_does_not_build_a_fresh_worker_for_an_idle_session(panel):
    panel.new_chat()
    session_id = panel.chat_sessions.current_id()
    assert session_id not in panel._workers

    panel.on_abort()

    assert session_id not in panel._workers


class _StuckProcess:
    """A `claude` that starts, writes nothing, and would sit there all day."""

    def __init__(self):
        import io

        self.stdin = io.StringIO()
        self.stderr = io.StringIO()
        self.returncode = 0
        self.killed = threading.Event()

    @property
    def stdout(self):
        self.killed.wait(10)
        return iter(())

    def kill(self):
        self.killed.set()

    def wait(self, timeout=None):
        return self.returncode


def test_stop_and_abort_kill_the_run_provider_process_without_waiting_the_ceiling(panel):
    """
    A `claude` call under the full ceiling would otherwise hold Stop
    and Abort for up to `_CEILING`'s 1500 seconds
    (`core/llm/claude.py :: _CEILING`); both must instead end the run's own
    live process at once.
    """
    from core.llm.claude import ClaudeCodeClient, kill

    class _StubSdkClient:
        pipeline = None

        def kill_provider(self):
            kill(self._live)

    a_turn = [{"role": "system", "content": "You are the Design phase."},
              {"role": "user", "content": "Make a 10mm plate"}]

    for action in ("stop", "abort"):
        panel.new_chat()
        bundle = panel._worker_for(panel.chat_sessions.current_id())

        process = _StuckProcess()
        stub = _StubSdkClient()
        stub._live = ClaudeCodeClient(model="sonnet", spawn=lambda argv, cwd: process)
        bundle.worker.sdk_client = stub

        done = threading.Event()

        def drive():
            list(stub._live.stream(a_turn))
            done.set()

        driver = threading.Thread(target=drive)
        driver.start()
        while not stub._live.run._live:
            time.sleep(0.01)

        started = time.monotonic()
        getattr(bundle.worker, action)()

        assert done.wait(5), "%s did not end the run in flight" % action
        assert time.monotonic() - started < 5
        assert process.killed.is_set()
        driver.join(timeout=1)


def test_an_answer_off_the_drawing_says_so_rather_than_no_values_given(panel):
    """
    A question put back to the sheet is answered without the user, and the
    transcript has to show that rather than falling through to "No values
    given, so the Plan will say what it assumed" — which would be a lie: a
    value read off the drawing is read, not assumed.
    """
    from core.events import ANSWER, Event

    panel.on_event(Event(ANSWER, {"text": "Front pocket — 20 mm deep — read (SECTION A-A)",
                                  "by": "drawing"}))
    QT_APP.processEvents()

    said = _said_in(panel)
    assert "Looked at the drawing again" in said
    assert "SECTION A-A" in said
    assert "No values given" not in said.split("Looked at the drawing again")[-1]


def test_an_attached_picture_stays_in_the_chat_after_the_chips_clear(panel, tmp_path):
    """
    `chip_bar.get_and_clear()` took the chips off the bar and nothing replaced
    them, so the user could not see what the run had been shown.

    The picture stands in a row of its own, pushed to the right margin where
    the Intent it came with stands, so it is looked for among a transcript
    entry's children rather than among the entries themselves.
    """
    from conftest import QtWidgets

    from core.events import ATTACHED, Event

    drawing = tmp_path / "bracket.png"
    _write_a_png(str(drawing))

    panel.on_event(Event(ATTACHED, {"files": [{"name": "bracket.png",
                                               "path": str(drawing)}]}))
    QT_APP.processEvents()

    shown = [label for widget in _widgets_in(panel)
             for label in widget.findChildren(QtWidgets.QLabel)
             if label.pixmap() is not None and not label.pixmap().isNull()]
    assert shown, "the picture is not in the transcript"
    assert shown[-1].toolTip() == "bracket.png"


def test_an_attachment_whose_file_has_gone_renders_its_name(panel):
    """
    A clipboard paste is a temp file the panel deletes when the run ends, so a
    Session opened tomorrow points at nothing. A renderer that raised would be
    swallowed by `EventStream.emit` and the whole transcript would lose it
    silently.
    """
    from core.events import ATTACHED, Event

    before = len(_widgets_in(panel))
    panel.on_event(Event(ATTACHED, {"files": [{"name": "pasted.png",
                                               "path": "/nowhere/pasted.png"}]}))
    QT_APP.processEvents()

    assert len(_widgets_in(panel)) == before + 1
    assert "pasted.png" in _said_in(panel)


def test_a_file_card_opens_the_file_it_names(monkeypatch):
    """
    A card in the transcript is what a user clicks to look at a drawing again,
    and the drawing is a PDF the panel has no viewer for: the file is handed to
    whatever the desktop opens it with, as a `file://` URL rather than a path.
    """
    from conftest import QtCore, QtGui

    from ui.upload_widget import FileChip

    opened = []
    monkeypatch.setattr(QtGui.QDesktopServices, "openUrl", lambda url: opened.append(url))
    chip = FileChip("/tmp/mm25-p1-1.pdf", removable=False)

    QtGui.QGuiApplication.sendEvent(chip, QtGui.QMouseEvent(
        QtCore.QEvent.MouseButtonRelease, QtCore.QPointF(4, 4),
        QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))

    assert [u.toLocalFile() for u in opened] == ["/tmp/mm25-p1-1.pdf"]
    assert chip.cursor().shape() == QtCore.Qt.PointingHandCursor, \
        "nothing says the card can be clicked"


def _write_a_png(path):
    """A file Qt itself will decode — the panel renders it through QPixmap."""
    from conftest import QtCore, QtGui

    picture = QtGui.QPixmap(8, 8)
    picture.fill(QtGui.QColor(QtCore.Qt.darkCyan))
    assert picture.save(path, "PNG")


def test_reasoning_streams_inline_and_never_pins_to_the_queue(panel):
    """
    The status line used to carry the last 90 characters of every
    reasoning chunk (`daedalus_panel.py:1073` before this change). Reasoning
    now only ever lands in its own accordion, in the chat scroll.
    """
    from core.events import Event, THOUGHT

    session_id, _ = panel.chat_sessions.add_new_chat("reasoning-run")
    panel.switch_session(session_id)
    before_text, before_visible = _now_on(panel), panel._queue_widget.isVisible()

    long_thought = "the plan looks reasonable so I will start from the base sketch " * 3
    panel.on_event(Event(THOUGHT, {"phase": "plan", "text": long_thought}))
    QT_APP.processEvents()

    assert _now_on(panel) == before_text
    assert panel._queue_widget.isVisible() == before_visible
    assert long_thought in _said_in(panel)


def test_a_wordless_thought_is_a_row_that_does_not_open(panel):
    """
    The subscription route says it is reasoning and withholds the words, so
    the row goes up to prove the run is alive — and offers nothing to open,
    since a row that opens onto an empty label is worse than one that says it
    has nothing.
    """
    from core.events import Event, THOUGHT
    from ui.accordion_widget import AccordionWidget

    session_id, _ = panel.chat_sessions.add_new_chat("wordless-run")
    panel.switch_session(session_id)

    panel.on_event(Event(THOUGHT, {"phase": "design", "text": ""}))
    QT_APP.processEvents()

    row = [w for w in _widgets_in(panel) if isinstance(w, AccordionWidget)][-1]
    row.toggle_content()

    assert not row.content_area.isVisible(), "a row with nothing in it opened"
    assert row.toggle_button.icon().isNull(), "it still promised a disclosure"


def test_a_thought_that_arrives_late_gets_its_chevron_back(panel):
    """A route that does stream the words turns the same row into a real one."""
    from core.events import Event, THOUGHT
    from ui.accordion_widget import AccordionWidget

    session_id, _ = panel.chat_sessions.add_new_chat("late-words-run")
    panel.switch_session(session_id)

    panel.on_event(Event(THOUGHT, {"phase": "design", "text": ""}))
    panel.on_event(Event(THOUGHT, {"phase": "design", "text": "the boss is the datum"}))
    QT_APP.processEvents()

    row = [w for w in _widgets_in(panel) if isinstance(w, AccordionWidget)][-1]
    row.toggle_content()

    assert row.content_area.isVisible()
    assert "the boss is the datum" in _said_in(panel)


def test_the_thinking_row_says_how_long_it_has_been_thinking(panel):
    """
    No route can say how long a model will take, so the panel says how long
    it has taken — the question a user asks a panel that has gone quiet.
    """
    from core.events import Event, THOUGHT
    from ui.accordion_widget import AccordionWidget

    session_id, _ = panel.chat_sessions.add_new_chat("slow-run")
    panel.switch_session(session_id)

    panel.on_event(Event(THOUGHT, {"phase": "design", "text": ""}))
    QT_APP.processEvents()
    row = [w for w in _widgets_in(panel) if isinstance(w, AccordionWidget)][-1]
    row._since -= 83
    row._say_how_long()

    assert "1:23" in row.toggle_button.text()
    assert "Thinking (design)" in row.toggle_button.text()


def test_the_words_a_phase_writes_open_a_row_of_their_own(panel):
    """
    The reply is not the reasoning: it forms after it, it is what the phase
    will be held to, and the wait it accounts for is the larger one. Its own
    row, so the two counts are read apart — a phase that thought for one
    minute and wrote for four is a different phase from the reverse.
    """
    from core.events import Event, THOUGHT, WRITING
    from ui.accordion_widget import AccordionWidget

    session_id, _ = panel.chat_sessions.add_new_chat("writing-run")
    panel.switch_session(session_id)

    panel.on_event(Event(THOUGHT, {"phase": "plan", "text": "two Steps, then"}))
    QT_APP.processEvents()
    thinking = [w for w in _widgets_in(panel) if isinstance(w, AccordionWidget)][-1]

    panel.on_event(Event(WRITING, {"phase": "plan", "text": '{"steps": ['}))
    panel.on_event(Event(WRITING, {"phase": "plan", "text": '"Pad the profile"]}'}))
    QT_APP.processEvents()
    writing = [w for w in _widgets_in(panel) if isinstance(w, AccordionWidget)][-1]

    assert writing is not thinking
    assert thinking._clock is None
    assert "Writing (plan)" in writing.toggle_button.text()
    assert '{"steps": ["Pad the profile"]}' in _said_in(panel)


def test_two_pages_of_one_drawing_each_keep_their_own_clock(panel):
    """
    Design fans out one call per page and the pages stream interleaved, so a
    row belongs to a phase and not to the panel. Held in one slot, the row was
    claimed by whichever page spoke last: a recorded run of the 2023 clamp
    read `Thinking (design: page 1)… 0:01` for a page whose own `COST` says it
    thought for 3:03, because page 2's first chunk landed 1.1 s after page 1's
    and took the slot.

    Each row is still a record and not a clock once its phase is over, which
    is what `COST` decides — per page, since page 2 was paid for 19 s before
    page 1 was.
    """
    from core.events import Event, COST, THOUGHT
    from ui.accordion_widget import AccordionWidget

    session_id, _ = panel.chat_sessions.add_new_chat("two-page-run")
    panel.switch_session(session_id)

    panel.on_event(Event(THOUGHT, {"phase": "design: page 1", "text": ""}))
    panel.on_event(Event(THOUGHT, {"phase": "design: page 2", "text": ""}))
    QT_APP.processEvents()
    rows = [w for w in _widgets_in(panel) if isinstance(w, AccordionWidget)][-2:]
    page_1, page_2 = rows

    assert "Thinking (design: page 1)" in page_1.toggle_button.text()
    assert "Thinking (design: page 2)" in page_2.toggle_button.text()
    assert page_1._clock is not None, "page 1 stopped counting when page 2 spoke"

    panel.on_event(Event(COST, {"phase": "design: page 2", "model": "sonnet",
                                "usd": 0.42, "seconds": 187.7}))
    QT_APP.processEvents()

    assert page_2._clock is None
    assert page_1._clock is not None, "page 2 being paid for stopped page 1"

    panel.on_event(Event(COST, {"phase": "design: page 1", "model": "sonnet",
                                "usd": 0.44, "seconds": 206.6}))
    QT_APP.processEvents()

    assert page_1._clock is None


def test_a_page_that_starts_writing_ends_only_its_own_reasoning(panel):
    """
    The reply of one page is not the end of another page's thinking: the
    recorded run put up four `Writing … 0:00` rows for two calls, each one the
    other page's row being taken away and given back.
    """
    from core.events import Event, THOUGHT, WRITING
    from ui.accordion_widget import AccordionWidget

    session_id, _ = panel.chat_sessions.add_new_chat("interleaved-run")
    panel.switch_session(session_id)

    panel.on_event(Event(THOUGHT, {"phase": "design: page 1", "text": ""}))
    panel.on_event(Event(THOUGHT, {"phase": "design: page 2", "text": ""}))
    QT_APP.processEvents()
    thinking_1, thinking_2 = [w for w in _widgets_in(panel)
                              if isinstance(w, AccordionWidget)][-2:]

    panel.on_event(Event(WRITING, {"phase": "design: page 2", "text": '{"design"'}))
    panel.on_event(Event(WRITING, {"phase": "design: page 1", "text": '{"design"'}))
    panel.on_event(Event(WRITING, {"phase": "design: page 2", "text": ': {}}'}))
    QT_APP.processEvents()
    written = [w for w in _widgets_in(panel) if isinstance(w, AccordionWidget)
               if "Writing" in w.toggle_button.text()]

    assert thinking_2._clock is None
    assert thinking_1._clock is None
    assert [w.toggle_button.text().split("…")[0] for w in written] == [
        "Writing (design: page 2)", "Writing (design: page 1)"]


def test_the_thinking_row_stops_counting_when_the_call_is_paid_for(panel):
    """
    `COST` is the event that says the model answered. Until it stopped the
    row, the count ran on into the loop's own work and stopped only when the
    next artefact happened to be rendered.
    """
    from core.events import Event, COST, THOUGHT
    from ui.accordion_widget import AccordionWidget

    session_id, _ = panel.chat_sessions.add_new_chat("paid-run")
    panel.switch_session(session_id)

    panel.on_event(Event(THOUGHT, {"phase": "design", "text": ""}))
    QT_APP.processEvents()
    row = [w for w in _widgets_in(panel) if isinstance(w, AccordionWidget)][-1]

    panel.on_event(Event(COST, {"phase": "design", "model": "opus", "seconds": 12.0}))
    QT_APP.processEvents()

    assert row._clock is None


def test_the_thinking_row_wears_the_brain_figma_puts_on_it(panel):
    """
    Figma's Reasoning header is a brain and its words; the chevron beside it
    is a disclosure mark and says nothing about what the row holds. A column
    of rows that all open, and all read `Thinking`/`Step 3 — ...`, is told
    apart at a glance by the mark or it is read line by line.

    The mark is a tinted pixmap rather than a glyph, for the reason the
    chevron is: a glyph is whatever the user's font decided, at whatever
    weight, and it does not take the icon tint (`_paint_chevron`).
    """
    from core.events import Event, THOUGHT, TOOL_CALL
    from ui.accordion_widget import AccordionWidget
    from ui.design_tokens import ICON

    session_id, _ = panel.chat_sessions.add_new_chat("brain-run")
    panel.switch_session(session_id)

    panel.on_event(Event(THOUGHT, {"phase": "design", "text": "measuring the flats"}))
    QT_APP.processEvents()
    thinking = [w for w in _widgets_in(panel) if isinstance(w, AccordionWidget)][-1]

    assert thinking.mark.isVisible(), "the reasoning row carries no mark"
    assert thinking.mark.pixmap().size().toTuple() == (ICON, ICON)
    assert thinking.mark.width() == ICON, (
        "the mark is %dpx wide and the words start that far in"
        % thinking.mark.width())

    panel.on_event(Event(TOOL_CALL, {"name": "run_script", "arguments": {"code": "pass"}}))
    QT_APP.processEvents()
    call = [w for w in _widgets_in(panel) if isinstance(w, AccordionWidget)][-1]

    assert not call.mark.isVisible(), \
        "a call the run made wears the brain too, so the mark tells nothing apart"


def test_reasoning_is_not_truncated_to_ninety_characters(panel):
    """The 90-character clip was the status line's; the accordion never had it."""
    from core.events import Event, THOUGHT
    from ui.accordion_widget import AccordionWidget

    session_id, _ = panel.chat_sessions.add_new_chat("long-reasoning-run")
    panel.switch_session(session_id)

    chunks = ["thinking about the base sketch and its constraints, " * 2,
              "then the fillet radius and how it interacts with the pocket depth " * 2]
    for chunk in chunks:
        panel.on_event(Event(THOUGHT, {"phase": "plan", "text": chunk}))
    QT_APP.processEvents()

    thinking = [w for w in _widgets_in(panel) if isinstance(w, AccordionWidget)
               and "Thinking" in w.toggle_button.text()]
    assert thinking, "no reasoning accordion was rendered"
    full_text = thinking[-1].content_area.text()
    assert full_text == "".join(chunks)
    assert len(full_text) > 90


def test_a_run_with_no_reasoning_shows_no_thinking_accordion(panel):
    """A model that emits no reasoning gets no empty placeholder for it."""
    from core.events import Event, INTENT, QUESTION
    from ui.accordion_widget import AccordionWidget

    session_id, _ = panel.chat_sessions.add_new_chat("no-reasoning-run")
    panel.switch_session(session_id)

    panel.on_event(Event(INTENT, {"text": "a plain box"}))
    panel.on_event(Event(QUESTION, {"text": "How tall?"}))
    QT_APP.processEvents()

    thinking = [w for w in _widgets_in(panel) if isinstance(w, AccordionWidget)
               and "Thinking" in w.toggle_button.text()]
    assert not thinking


def test_a_tool_result_still_says_what_came_back_after_it_is_timed(panel):
    """
    The duration goes on the call's own row. What the call answered
    is a separate sentence, and the Queue's line is where it has always gone —
    timing a row must not swallow it.
    """
    from core.events import Event, INTENT, TOOL_CALL, TOOL_RESULT

    panel.on_event(Event(kind=INTENT, data={"text": "a plain box"}))
    panel.on_event(Event(kind=TOOL_CALL, data={"name": "snapshot", "arguments": {}}))
    panel.on_event(Event(kind=TOOL_RESULT,
                         data={"name": "snapshot", "result": {"status": "empty document"}}))
    QT_APP.processEvents()

    said = panel._queue_widget._now_label._full
    assert "snapshot" in said and "empty document" in said, said


def test_a_tool_call_row_carries_how_long_the_call_took(panel):
    from core.events import Event, INTENT, TOOL_CALL, TOOL_RESULT
    from ui.accordion_widget import AccordionWidget

    panel.on_event(Event(kind=INTENT, data={"text": "a plain box"}))
    panel.on_event(Event(kind=TOOL_CALL, data={"name": "base_inventory", "arguments": {}},
                         at=1000.0))
    panel.on_event(Event(kind=TOOL_RESULT,
                         data={"name": "base_inventory", "result": {"status": "done"}},
                         at=1003.2))
    QT_APP.processEvents()

    history = panel.chat_sessions.get_current_chat_history()
    titles = [row.toggle_button.text()
              for row in history.widget().findChildren(AccordionWidget)]
    assert any("(3.2s)" in title for title in titles), titles


def test_the_end_of_a_run_reaches_the_panel_on_the_gui_thread(panel):
    """
    The crash this addon shipped with: `LoopWorker` is moved onto a QThread,
    and `finished` was connected to a lambda closing over the transcript. Qt
    takes the sender as the context of a connection made to a plain callable,
    so the comparison was the worker thread against itself, the connection
    resolved to a direct call, and `on_run_finished` ran on the worker thread
    -- `_set_running`, then `_queue_widget.now(...)`. FreeCAD printed sixteen
    `QObject::setParent: ... different thread` warnings and one
    `QObject::killTimer: ... another thread`, then segfaulted inside
    `queue_widget.py :: _Elided.set_full`.

    Fired through the worker's own event loop rather than from here, because
    emitting on the GUI thread is the one case that passes either way.
    """
    from conftest import QtCore

    log = panel.chat_sessions.current_id()
    bundle = panel._worker_for(log)
    main = QtCore.QThread.currentThread()
    ran_on = {}

    saved = panel._set_running
    panel._set_running = lambda running: ran_on.setdefault(
        "slot", QtCore.QThread.currentThread())

    class Fire(QtCore.QObject):
        @QtCore.Slot()
        def go(self):
            ran_on["emitted_from"] = QtCore.QThread.currentThread()
            bundle.worker.finished.emit(None, log)

    fire = Fire()
    fire.moveToThread(bundle.thread)
    try:
        QtCore.QMetaObject.invokeMethod(fire, "go", QtCore.Qt.QueuedConnection)
        deadline = QtCore.QElapsedTimer()
        deadline.start()
        while len(ran_on) < 2 and deadline.elapsed() < 10000:
            QT_APP.processEvents()
    finally:
        panel._set_running = saved

    assert ran_on.get("emitted_from") not in (None, main), \
        "the signal has to be emitted from the worker thread for this to mean anything"
    assert ran_on.get("slot") == main


def test_the_panel_marshals_the_document_onto_the_gui_thread(panel):
    """
    The first Intent of a Session segfaults FreeCAD without this.

    `LoopWorker.do_work` runs on a QThread and the first thing it does is
    `pipeline.lock()`, which disables the main window. `sdk.Client` defaults
    `dispatch` to running the callable where it stands -- right for a headless script, a Document
    mutation and a `QWidget.setEnabled` off the GUI thread here.
    """
    from bridge.main_thread import on_main_thread

    client = panel.worker._get_sdk_client()

    assert client.dispatch is on_main_thread
    assert client.pipeline._dispatch is on_main_thread


def test_daedalus_panel_has_no_dead_request_work_signal():
    """
    `DaedalusPanel.request_work` only ever had test subscribers —
    the real work goes through the Session's own `bundle.request_work`.
    """
    from ui.daedalus_panel import DaedalusPanel

    assert not hasattr(DaedalusPanel, "request_work")


def test_the_dispatch_table_names_only_kinds_the_stream_registers(panel):
    """
    `on_event` draws a kind through its table or falls through to `narrate`;
    a table key the stream does not register would never be reached.
    """
    from core import events as ev
    from ui.daedalus_panel import DaedalusPanel

    panel.new_chat()
    panel.on_event(ev.Event(ev.CALL_STARTED, {}))

    assert set(DaedalusPanel._ON_EVENT_DISPATCH) <= set(ev.KINDS)


def test_the_intent_shows_at_once_while_the_message_is_routed(panel, monkeypatch):
    """
    Routing is a whole model call before the run's own `INTENT` arrives, so
    the user's words go up on Send and the Queue says it is routing; the
    `INTENT` that follows does not put the same words up a second time.
    """
    from core.events import INTENT, ROUTED, Event

    panel.new_chat()
    _never_call_the_real_model(panel)
    monkeypatch.setattr(panel, "_bind_document", lambda bundle: None)

    panel.user_input.setPlainText("a flanged bushing")
    try:
        panel.on_send()
        assert "a flanged bushing" in _said_in(panel)
        assert _now_on(panel) == "Routing\u2026"

        panel.on_event(Event(ROUTED, {"route": "design"}))
        panel.on_event(Event(INTENT, {"text": "a flanged bushing"}))
    finally:
        panel._set_running(False)
        panel._worker_for(panel.chat_sessions.current_id()).running = False

    shown = _said_in(panel)
    assert shown.count("a flanged bushing") == 1
    assert "unrecognized" not in shown


def test_a_step_that_faults_ends_its_own_run_and_the_gui_survives(panel, monkeypatch):
    """
    A Step that faults must end its own run, not take the process
    down with it. `send()` raising is `do_work`'s worst case -- it still has
    to emit `finished` with no result and still has to call `pipeline.end_run`,
    so the Document is unlocked and the panel is left usable for the next
    Intent rather than stuck mid-run.
    """
    import types

    from ui.daedalus_panel import LoopWorker, EventBridge, AskTheUser

    worker = LoopWorker(EventBridge(), AskTheUser())

    ended = []

    class _FakePipeline:
        def lock(self):
            return None

        def end_run(self):
            ended.append(True)

    class _FakeClient:
        def __init__(self):
            self.pipeline = _FakePipeline()
            self.dispatch = lambda func, timeout=None: func()

        def domain_for(self, intent, choice, events):
            return "part"

        def send(self, *args, **kwargs):
            raise RuntimeError("a Step faulted inside the kernel")

    fake_registry = types.SimpleNamespace(call=lambda *a, **k: {"document": ""})
    monkeypatch.setattr(worker, "_get_sdk_client", lambda: _FakeClient())
    monkeypatch.setattr("sdk.build_registry", lambda **kwargs: fake_registry)

    finished = []
    worker.finished.connect(lambda result, transcript: finished.append(result))

    worker.do_work("break something", [], "")

    assert finished == [None], "a faulting Step still ends the run rather than raising out"
    assert ended == [True], "end_run must still run so the Document is left unlocked"


def test_stop_with_no_sdk_client_yet_is_a_no_op(monkeypatch):
    """A Stop before any Intent has built an `sdk_client` has nothing to kill."""
    from ui.daedalus_panel import LoopWorker, EventBridge, AskTheUser

    worker = LoopWorker(EventBridge(), AskTheUser())
    monkeypatch.setattr(worker.ask, "abandon", lambda: None)

    worker.stop()
    worker.abort()


def test_an_unopenable_transcript_is_logged_not_swallowed(monkeypatch, tmp_path):
    """
    A missing transcript log must not cost the user their run, but it must
    say why it is missing. The bare `except: pass` here left five
    Sessions with no transcript at all and no line anywhere naming the cause.
    """
    import types

    from bridge import logger as logger_module
    from ui.daedalus_panel import LoopWorker, EventBridge, AskTheUser

    worker = LoopWorker(EventBridge(), AskTheUser())

    class _FakePipeline:
        def lock(self):
            return None

        def end_run(self):
            return None

    class _FakeClient:
        def __init__(self):
            self.pipeline = _FakePipeline()
            self.dispatch = lambda func, timeout=None: func()

        def domain_for(self, intent, choice, events):
            return "part"

        def send(self, *args, **kwargs):
            return types.SimpleNamespace(ok=True)

    fake_registry = types.SimpleNamespace(call=lambda *a, **k: {"document": ""})
    monkeypatch.setattr(worker, "_get_sdk_client", lambda: _FakeClient())
    monkeypatch.setattr("sdk.build_registry", lambda **kwargs: fake_registry)

    errors = []
    monkeypatch.setattr(logger_module.logger, "error", lambda msg: errors.append(msg))

    unopenable = str(tmp_path / "does-not-exist" / "session.jsonl")
    finished = []
    worker.finished.connect(lambda result, transcript: finished.append(transcript))

    worker.do_work("do something", [], unopenable)

    assert finished == [unopenable], "an unopenable transcript must not stop the run"
    assert any(unopenable in msg for msg in errors), "the failure must name the path"


def test_an_intent_from_the_panel_is_routed_before_design(monkeypatch):
    """
    Every Intent the panel sends reaches the orchestrator first, the same as
    an SDK caller's. `route_first=False` here made the classifier dead code
    in the GUI: a run question or a Stop typed into the composer was built
    as a design request.
    """
    import types

    from ui.daedalus_panel import LoopWorker, EventBridge, AskTheUser

    worker = LoopWorker(EventBridge(), AskTheUser())
    sent = []

    class _FakePipeline:
        def lock(self):
            return None

        def end_run(self):
            return None

    class _FakeClient:
        def __init__(self):
            self.pipeline = _FakePipeline()
            self.dispatch = lambda func, timeout=None: func()

        def domain_for(self, intent, choice, events):
            return "part"

        def send(self, *args, **kwargs):
            sent.append(kwargs)
            return types.SimpleNamespace(ok=True)

    fake_registry = types.SimpleNamespace(call=lambda *a, **k: {"document": ""})
    monkeypatch.setattr(worker, "_get_sdk_client", lambda: _FakeClient())
    monkeypatch.setattr("sdk.build_registry", lambda **kwargs: fake_registry)

    worker.do_work("is it done yet?", [], "")

    assert sent, "the panel must reach the SDK at all"
    assert sent[0].get("route_first", True) is not False, (
        "the panel must let the orchestrator classify the Intent, not force Design")


@pytest.fixture
def the_domain_choice(monkeypatch):
    import sdk

    stored = {"domain": "auto"}
    monkeypatch.setattr(sdk, "get_domain", lambda: stored["domain"])
    monkeypatch.setattr(sdk, "set_domain", lambda choice: stored.__setitem__("domain", choice))
    return stored


def test_the_composer_offers_auto_part_assembly_and_bim_and_starts_on_auto(panel, the_domain_choice, monkeypatch):
    offered = []
    monkeypatch.setattr(panel.domain_selector, "open_at", lambda levels, anchor: offered.extend(levels))

    panel._show_domain()
    panel._open_domain_selector()

    assert panel.domain_button.text() == "Auto"
    assert offered == ["Auto", "Part", "Assembly", "BIM"]


def test_picking_a_domain_stores_it_and_shows_it(panel, the_domain_choice):
    panel._on_domain_selected("assembly")

    assert the_domain_choice["domain"] == "assembly"
    assert panel.domain_button.text() == "Assembly"


def test_picking_bim_shows_it_in_capitals(panel, the_domain_choice):
    panel._on_domain_selected("bim")

    assert the_domain_choice["domain"] == "bim"
    assert panel.domain_button.text() == "BIM"


def test_the_pill_reads_what_was_stored(panel, the_domain_choice):
    the_domain_choice["domain"] = "part"

    panel._show_domain()

    assert panel.domain_button.text() == "Part"


def test_the_chat_says_the_domain_before_the_loop_starts(panel):
    from core.events import DOMAIN, Event
    from ui.chat_widgets import AssistantMessageWidget

    panel.on_event(Event(DOMAIN, {"domain": "assembly", "inferred": True}))
    panel.on_event(Event(DOMAIN, {"domain": "part", "inferred": False}))

    said = [w.text_label.text() for w in panel.findChildren(AssistantMessageWidget)]
    assert "Domain: Assembly (inferred)" in said
    assert "Domain: Part" in said


def test_the_run_is_sent_under_the_domain_the_choice_resolved_to(monkeypatch):
    import types

    from ui.daedalus_panel import LoopWorker, EventBridge, AskTheUser

    worker = LoopWorker(EventBridge(), AskTheUser())
    sent, asked = [], []

    class _FakePipeline:
        def lock(self):
            return None

        def end_run(self):
            return None

    class _FakeClient:
        def __init__(self):
            self.pipeline = _FakePipeline()
            self.dispatch = lambda func, timeout=None: func()

        def domain_for(self, intent, choice, events):
            asked.append((intent, choice))
            return "assembly"

        def send(self, *args, **kwargs):
            sent.append(kwargs)
            return types.SimpleNamespace(ok=True)

    monkeypatch.setattr(worker, "_get_sdk_client", lambda: _FakeClient())
    monkeypatch.setattr("sdk.build_registry",
                        lambda **kwargs: types.SimpleNamespace(call=lambda *a, **k: {"document": ""}))
    monkeypatch.setattr("sdk.get_domain", lambda: "auto")

    worker.do_work("join the plates", [], "")

    assert asked == [("join the plates", "auto")]
    assert sent[0]["domain"] == "assembly"


def test_what_the_domain_call_cost_stays_in_the_runs_total(panel):
    from core.events import COST, INTENT, Event

    panel.new_chat()
    panel.on_event(Event(COST, {"phase": "domain", "usd": 0.01, "model": "m", "seconds": 1.0}))
    panel.on_event(Event(INTENT, {"text": "join the plates"}))
    panel.on_event(Event(COST, {"phase": "execute", "usd": 0.04, "model": "m", "seconds": 1.0}))

    assert panel.usage_label.text().startswith("$0.05")


def test_a_failed_domain_call_ends_the_run_with_an_error_in_the_chat(monkeypatch):
    import types

    from ui.daedalus_panel import LoopWorker, EventBridge, AskTheUser

    worker = LoopWorker(EventBridge(), AskTheUser())
    seen, finished, ended = [], [], []

    class _FakePipeline:
        def lock(self):
            return None

        def end_run(self):
            ended.append(True)

    class _FakeClient:
        def __init__(self):
            self.pipeline = _FakePipeline()
            self.dispatch = lambda func, timeout=None: func()

        def domain_for(self, intent, choice, events):
            raise RuntimeError("quota exceeded")

        def send(self, *args, **kwargs):
            raise AssertionError("the run must not start")

    monkeypatch.setattr(worker, "_get_sdk_client", lambda: _FakeClient())
    monkeypatch.setattr("sdk.build_registry",
                        lambda **kwargs: types.SimpleNamespace(call=lambda *a, **k: {"document": ""}))
    monkeypatch.setattr("sdk.get_domain", lambda: "auto")
    monkeypatch.setattr(worker.bridge, "subscriber", lambda: seen.append)
    worker.finished.connect(lambda result, transcript: finished.append(result))

    worker.do_work("join the plates", [], "")

    assert finished == [None]
    assert ended == [True]
    assert any(e.kind == "error" and "quota exceeded" in e.data["message"] for e in seen)
