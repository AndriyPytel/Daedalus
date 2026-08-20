"""
The panel, built for real against FreeCAD's own Qt binding, offscreen.

Most of the panel stays Level 3 — how a widget *looks* under a theme is not
something a test can tell you, which is exactly the finding behind ADR-0011.
What is checked here is the arithmetic and the state machine that would
otherwise be verified by opening the panel and squinting: the composer's
height, Send and Stop sharing one slot, and Sessions surviving a restart —
restored, renamed, deleted, and let go of when the user switches away.

The bound itself — pruning on create, and a promptless launch counting for
nothing — is Level 1 now that the Sessions are not behind a widget. See
tests/test_sessions.py; what stays here is the stack keeping step with them.
"""
import os

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
    from utils import settings_manager

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
    from ui.copilot_panel import CopilotPanel

    built = CopilotPanel()
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


def test_send_and_stop_share_one_slot(panel):
    """Never both, so the control strip does not shift while a run starts."""
    panel._set_running(True)
    assert (panel.stop_button.isVisible(), panel.send_button.isVisible()) == (True, False)

    panel._set_running(False)
    assert (panel.stop_button.isVisible(), panel.send_button.isVisible()) == (False, True)


def test_the_composer_shows_the_figma_placeholder(panel):
    assert panel.user_input.placeholderText() == "Describe what you want to build\u2026"


def test_the_composer_disables_and_greys_out_while_a_run_is_going(panel):
    """
    Greyed the way `mark_as_reasoning` dims a subdued accordion: an opacity
    effect, not a colour, since the theme still decides what "disabled" looks
    like (ADR-0011).
    """
    from conftest import QtWidgets

    panel._set_running(True)
    assert not panel.user_input.isEnabled()
    effect = panel.user_input.graphicsEffect()
    assert isinstance(effect, QtWidgets.QGraphicsOpacityEffect)
    assert effect.opacity() < 1.0

    panel._set_running(False)
    assert panel.user_input.isEnabled()
    assert panel.user_input.graphicsEffect() is None


def test_the_composer_re_enables_at_a_gate_and_disables_once_it_is_answered(panel, monkeypatch):
    """
    A Gate's own answer is typed in the composer (Amend, the values Plan was
    not given), so it must come back before the card does — and go dark again
    the moment the run resumes, since nothing is being asked while it runs.
    """
    monkeypatch.setattr(panel.ask, "answer", lambda answer: None)

    panel._set_running(True)
    assert not panel.user_input.isEnabled()

    panel.on_missing_values("How many teeth?")
    QT_APP.processEvents()
    assert panel.user_input.isEnabled(), "the composer is where the gate's answer is typed"

    panel.user_input.setPlainText("20 teeth, module 2")
    _button(panel._question_card, "Send").click()
    QT_APP.processEvents()

    assert not panel.user_input.isEnabled(), "the run resumed but the composer stayed open"

    panel._set_running(False)


def test_stopping_a_gate_answer_leaves_the_composer_dark_again(panel):
    """Stop is also "the run still has the floor" — the card it takes down
    must not leave the composer looking answerable a moment longer."""
    panel._set_running(True)
    panel.on_missing_values("How many teeth?")
    QT_APP.processEvents()
    assert panel.user_input.isEnabled()

    panel.on_stop()
    QT_APP.processEvents()

    assert not panel.user_input.isEnabled()
    panel._set_running(False)


@pytest.mark.parametrize("status", [
    "Step 1/9: Create a sketch on the XY plane and draw the two circles at the computed centre",
    "Thinking: " + "the plan looks reasonable so I will start from the base sketch " * 2,
    "Applied → " + "A" * 300,
])
def test_a_long_status_line_does_not_widen_the_panel(panel, status):
    """
    The status line is outside the transcript's QScrollArea, so its minimum
    width is the panel's. Unwrapped, a Step description made the dock wider —
    and Qt does not give the width back when the text goes short again, so the
    user lost the dock width they had set. FCC-21.

    The dock's own minimumSizeHint only follows its child once shown, which
    the fixture's panel is not. The child is where the width is decided.
    """
    contents = panel.widget()
    panel.status_line.setVisible(True)
    panel.status_line.setText("Working…")
    narrow = contents.minimumSizeHint().width()

    panel.status_line.setText(status)

    assert contents.minimumSizeHint().width() == narrow, \
        "the panel's minimum width follows the status line's text"


def test_the_empty_chat_says_where_the_user_stands(panel):
    """
    Freshly built rather than `panel._empty_state` itself: the model fetch
    that landed since the panel opened may already have moved the button off
    whatever it showed when the transcript's own empty state was captured,
    and that race is not what this test is for.
    """
    text = panel._build_empty_state().text()
    assert "Plan Gate" in text
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


def _mock_no_stored_model(monkeypatch):
    """
    The developer's own machine has a real stored model, in real FreeCAD
    settings, that has nothing to do with `_TWO_PROVIDERS` below — read it
    back and `_refresh_models` reaches for `_retire_model`, which would
    overwrite that real setting with one of this test's fake ids.
    """
    from utils import settings_manager

    monkeypatch.setattr(settings_manager.instance(), "get_last_model", lambda: "")


def test_clicking_the_model_button_opens_the_provider_step(panel, monkeypatch):
    """FCC-175: the popup opens on a flat list of providers, not their models."""
    from ui.model_selector import PROVIDER_STEP

    _mock_no_stored_model(monkeypatch)
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

    _mock_no_stored_model(monkeypatch)
    panel._refresh_models(_TWO_PROVIDERS)
    panel._open_model_selector()
    QT_APP.processEvents()

    _selector_click(panel.model_selector, "OpenAI")
    QT_APP.processEvents()

    assert panel.model_selector.step == MODEL_STEP
    assert panel.model_selector.visible_rows() == ["GPT-4o", "o1"]
    panel.model_selector.close()


def test_choosing_a_model_closes_the_popup_and_updates_the_panel(panel, monkeypatch):
    from utils import settings_manager

    _mock_no_stored_model(monkeypatch)
    written = []
    monkeypatch.setattr(settings_manager.instance(), "set_last_model", written.append)

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
    assert written == ["claude-opus-4"]


def test_reopening_the_selector_always_starts_at_the_provider_step(panel, monkeypatch):
    """
    Reopening after a model is picked shows providers again, not the
    last-selected provider's models (FCC-175's Acceptance Criteria).
    """
    from utils import settings_manager
    from ui.model_selector import PROVIDER_STEP

    _mock_no_stored_model(monkeypatch)
    monkeypatch.setattr(settings_manager.instance(), "set_last_model", lambda model_id: None)

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


def test_the_button_shows_a_stored_model_no_static_list_knows(panel, monkeypatch):
    """
    The stored id lives only in the provider's fetched list, which has not
    landed when the panel is built. Falling through to the first available
    model — GPT-4o — is FCC-41.
    """
    from utils import provider_manager, settings_manager

    monkeypatch.setattr(provider_manager.instance(), "awaiting_model_lists", lambda: True)
    monkeypatch.setattr(settings_manager.instance(), "get_last_model", lambda: "gpt-5.6-sol")

    panel._refresh_models([{"id": "gpt-4o", "display": "GPT-4o", "provider": "OpenAI"}])

    assert panel._current_model_id == "gpt-5.6-sol"


def test_a_failed_fetch_leaves_its_provider_still_awaited(monkeypatch):
    """
    A provider that could not be reached has not said the stored model is gone.
    Treating a failure as an answer drops the user's model on a flaky network.
    """
    from utils import provider_manager, settings_manager

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
    from utils import provider_manager, settings_manager

    settings = settings_manager.instance()
    written = []
    monkeypatch.setattr(provider_manager.instance(), "awaiting_model_lists", lambda: False)
    monkeypatch.setattr(settings, "get_last_model", lambda: "gpt-5.6-sol")
    monkeypatch.setattr(settings, "get_default_model", lambda: "gpt-4o")
    monkeypatch.setattr(settings, "set_last_model", written.append)

    panel._refresh_models([{"id": "gpt-4o", "display": "GPT-4o", "provider": "OpenAI"}])

    assert panel._current_model_id == "gpt-4o"
    assert written == ["gpt-4o"]
    assert panel.status_line.isVisible()
    assert "gpt-5.6-sol" in panel.status_line.text()


def test_retiring_a_model_with_no_preferences_default_still_names_one(panel, monkeypatch):
    """The default model is a preference, and it may never have been set."""
    from utils import provider_manager, settings_manager

    settings = settings_manager.instance()
    monkeypatch.setattr(provider_manager.instance(), "awaiting_model_lists", lambda: False)
    monkeypatch.setattr(settings, "get_last_model", lambda: "gpt-5.6-sol")
    monkeypatch.setattr(settings, "get_default_model", lambda: "")
    monkeypatch.setattr(settings, "set_last_model", lambda model_id: None)

    panel._refresh_models([{"id": "gpt-4o", "display": "GPT-4o", "provider": "OpenAI"}])

    assert panel._current_model_id == "gpt-4o"
    assert "switched to gpt-4o" in panel.status_line.text()


def test_send_hands_the_attachments_over_and_clears_the_bar(panel, tmp_path):
    """
    The panel used to collect the attached paths and drop them on the floor.
    What it emits is what the run is given. FCC-4.
    """
    picture = tmp_path / "bracket.png"
    picture.write_bytes(b"a file; whether it decodes is the loop's problem")

    sent = []
    panel.request_work.connect(lambda intent, files, log: sent.append((intent, files)))
    try:
        panel.chip_bar.add_file(str(picture))
        panel.user_input.setPlainText("Build this bracket")
        panel.on_send()
    finally:
        panel.request_work.disconnect()
        panel.worker.stop()
        panel._set_running(False)

    assert sent == [("Build this bracket", [str(picture)])]
    assert not panel.chip_bar.isVisible(), "the next Intent would reuse this one's files"


def test_an_unusable_attachment_stops_the_run_before_it_starts(panel, tmp_path, monkeypatch):
    """
    Told before the run, not after Plan already ignored it — and the chips stay
    on the bar, so the offending one can be taken off and the Intent sent
    again. FCC-14.
    """
    from core import llm

    monkeypatch.setattr(llm, "selected_model_name", lambda _=None: "openai/gpt-3.5-turbo")
    drawing = tmp_path / "bracket.step"
    drawing.write_text("ISO-10303-21;")
    panel.chip_bar.add_file(str(drawing))

    sent = []
    panel.request_work.connect(lambda intent, files, log: sent.append(intent))
    try:
        panel.user_input.setPlainText("Build this bracket")
        panel.on_send()
    finally:
        panel.request_work.disconnect()

    assert sent == [], "the run started with an attachment it could not use"
    assert panel.chip_bar.files() == [str(drawing)], "the user cannot take the chip off"
    assert panel.user_input.toPlainText() == "Build this bracket"
    said = _said_in(panel)
    assert "bracket.step" in said and "PNG" in said
    assert "cannot accept images" in said, "the model's own limit went unmentioned"

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


def test_shutdown_does_not_wait_for_a_question_nobody_will_answer(sessions_are_the_tests_own):
    """
    A run parked on the Plan Gate blocks its thread on an Event. `quit()` does
    not touch a thread that is inside a call, so waiting on it without first
    releasing the question is FreeCAD hanging on exit with no window left to
    answer from.

    In its own daemon thread: the defect under test is a wait that never
    returns, and a test that hangs says nothing to whoever runs the suite.
    """
    import time

    from conftest import QtCore
    from ui.copilot_panel import CopilotPanel

    built = CopilotPanel()
    blocked = QtCore.QTimer()
    blocked.moveToThread(built.thread)
    blocked.setSingleShot(True)
    blocked.timeout.connect(lambda: built.ask.about_the_plan(["a step"]),
                            QtCore.Qt.DirectConnection)
    QtCore.QMetaObject.invokeMethod(blocked, "start", QtCore.Qt.QueuedConnection,
                                    QtCore.Q_ARG(int, 0))
    deadline = time.time() + 5
    while not built.ask._answered.is_set() and built.ask._answer is None and time.time() < deadline:
        QT_APP.processEvents()
        time.sleep(0.02)

    import threading

    done = threading.Event()
    threading.Thread(target=lambda: (built._cleanup_thread(), done.set()),
                     daemon=True).start()

    assert done.wait(5), "shutdown is still waiting on a run parked at the Plan Gate"
    assert not built.thread.isRunning(), "the worker thread outlived the panel"


def test_the_panel_docks_on_the_main_window_once(sessions_are_the_tests_own):
    """
    Like the Tasks panel: it is a fixture of the window, so it is there on
    whatever workbench the user is working in. Installing twice — two
    workbench switches — must not build a second panel.
    """
    from conftest import QtWidgets
    from ui import copilot_dock

    window = QtWidgets.QMainWindow()
    copilot_dock.forget()
    try:
        panel = copilot_dock.install(window)
        again = copilot_dock.install(window)

        assert again is panel, "a second install built a second panel"
        assert panel.parent() is window, "the panel is not docked on the main window"
        assert window.findChildren(type(panel)) == [panel]
    finally:
        panel._cleanup_thread()
        copilot_dock.forget()
        window.deleteLater()


def test_leaving_the_copilot_workbench_does_not_take_the_chat_away():
    """
    Hiding the panel on `Deactivated` is what tied the chat to one workbench.
    Read from the source: `Deactivated` needs a running FreeCAD GUI to call.
    """
    import ast
    import os

    from ui import copilot_dock

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(copilot_dock.__file__))),
                        "Mod", "CopilotWorkbench.py")
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), path)
    deactivated = [node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef) and node.name == "Deactivated"]

    assert deactivated, "CopilotWorkbench lost its Deactivated hook"
    hides = [node for node in ast.walk(deactivated[0])
             if isinstance(node, ast.Attribute) and node.attr in ("hide", "setVisible", "close")]
    assert hides == [], "leaving the workbench still hides the chat"


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
    FreeCAD theme, and a colour of the addon's own, which ADR-0011 forbids.

    A theme is one global stylesheet, which is what the colour has to arrive
    through — the route ADR-0011 describes, not a QPalette call.
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


def test_a_session_with_no_intent_is_not_in_the_history_index(panel):
    """
    Fifty promptless launches would otherwise evict fifty real Sessions:
    load() keeps the newest KEEP and deletes the log of everything past it.
    """
    from utils import settings_manager

    before = settings_manager.instance().get_sessions()

    panel.chat_sessions.add_new_chat("New Chat")

    assert settings_manager.instance().get_sessions() == before


def _rows_in_the_picker(panel):
    """The names the popup is actually showing, in order."""
    from ui.history_popup import HistoryItem

    panel.toggle_history()
    try:
        rows = [panel.history_popup.list_layout.itemAt(i).widget()
                for i in range(panel.history_popup.list_layout.count())]
        return [(row.index, row.title_label.text())
                for row in rows if isinstance(row, HistoryItem)]
    finally:
        panel.history_popup.close()


def test_a_session_with_no_intent_is_not_in_the_picker_either(panel):
    """
    QA against FreeCAD 1.1.3 found the invariant enforced in the durable index
    and not in the picker: `toggle_history` built the popup from every Session
    in the stack, so a fresh dock showed one `New Chat` row against an index
    holding none of it.
    """
    index, _ = panel.chat_sessions.add_new_chat("New Chat")

    assert "New Chat" not in [name for _, name in _rows_in_the_picker(panel)]

    panel.chat_sessions.begin(index, "Two-bore bracket")

    assert "Two-bore bracket" in [name for _, name in _rows_in_the_picker(panel)]


def test_a_promptless_launch_adds_no_row_to_the_picker(panel):
    """Three launches where nobody typed anything, as the QA run did."""
    index, _ = panel.chat_sessions.add_new_chat("Real work")
    panel.chat_sessions.begin(index, "Two-bore bracket")
    before = _rows_in_the_picker(panel)

    for _ in range(3):
        panel.chat_sessions.add_new_chat("New Chat")

    assert [name for _, name in _rows_in_the_picker(panel)] == \
        [name for _, name in before]


def test_a_picker_row_names_the_session_it_shows_not_its_own_position(panel):
    """
    The picker shows a subset, so a row numbered from zero would open its
    neighbour. The row carries the Session's position in the stack. The first
    Session here is never used, so it stays unindexed and the picker skips it.
    """
    panel.chat_sessions.add_new_chat("Never used")
    index, _ = panel.chat_sessions.add_new_chat("New Chat")
    panel.chat_sessions.begin(index, "The one this test is looking for")

    rows = _rows_in_the_picker(panel)
    position, name = next((p, n) for p, n in rows
                          if n == "The one this test is looking for")

    assert position == index
    assert panel.chat_sessions.get_session_info(position)["name"] == name


def test_the_first_intent_puts_the_session_in_history_under_a_name(panel):
    from utils import settings_manager

    index, _ = panel.chat_sessions.add_new_chat("New Chat")

    panel.chat_sessions.begin(index, "Two-bore bracket")

    stored = settings_manager.instance().get_sessions()
    assert stored[-1]["name"] == "Two-bore bracket"
    assert panel.chat_sessions.get_session_info(index)["name"] == "Two-bore bracket"


def test_a_session_only_enters_history_once(panel):
    """The second Intent must not add a second row for the same Session."""
    from utils import settings_manager

    index, _ = panel.chat_sessions.add_new_chat("New Chat")
    panel.chat_sessions.begin(index, "first")
    count = len(settings_manager.instance().get_sessions())

    assert not panel.chat_sessions.begin(index, "second")
    assert len(settings_manager.instance().get_sessions()) == count
    assert panel.chat_sessions.get_session_info(index)["name"] == "first"


def test_a_generated_title_replaces_the_fallback_but_only_once(panel):
    index, _ = panel.chat_sessions.add_new_chat("New Chat")
    panel.chat_sessions.begin(index, "make me a bracket with tw…")
    log = panel.chat_sessions.get_session_info(index)["log"]

    assert panel.chat_sessions.title_arrived(log, "Two-bore bracket")
    assert not panel.chat_sessions.title_arrived(log, "Something else")
    assert panel.chat_sessions.get_session_info(index)["name"] == "Two-bore bracket"


def test_a_title_lands_on_the_session_that_asked_for_it(panel):
    """
    The call outlives the Session's position in the stack. Keyed by the index,
    deleting a Session while a title was in flight renames its neighbour.
    """
    doomed, _ = panel.chat_sessions.add_new_chat("New Chat")
    panel.chat_sessions.begin(doomed, "the one that gets deleted")
    asker, _ = panel.chat_sessions.add_new_chat("New Chat")
    panel.chat_sessions.begin(asker, "the one that asked")
    log = panel.chat_sessions.get_session_info(asker)["log"]

    panel.chat_sessions.remove_session(doomed)

    assert panel.chat_sessions.title_arrived(log, "Two-bore bracket")
    moved = next(i for i in range(panel.chat_sessions.count())
                 if panel.chat_sessions.get_session_info(i)["log"] == log)
    assert panel.chat_sessions.get_session_info(moved)["name"] == "Two-bore bracket"


def test_a_manual_rename_is_never_overwritten_by_a_generated_title(panel):
    index, _ = panel.chat_sessions.add_new_chat("New Chat")
    panel.chat_sessions.begin(index, "make me a bracket with tw…")
    log = panel.chat_sessions.get_session_info(index)["log"]

    panel.chat_sessions.rename_session(index, "mine")

    assert not panel.chat_sessions.title_arrived(log, "Two-bore bracket")
    assert panel.chat_sessions.get_session_info(index)["name"] == "mine"


def test_a_manual_rename_before_the_first_intent_also_wins(panel):
    """The generated title lands later, and the name it would replace is the user's."""
    index, _ = panel.chat_sessions.add_new_chat("New Chat")

    panel.chat_sessions.rename_session(index, "mine")
    panel.chat_sessions.begin(index, "the truncated intent")
    log = panel.chat_sessions.get_session_info(index)["log"]

    assert panel.chat_sessions.get_session_info(index)["name"] == "mine"
    assert not panel.chat_sessions.title_arrived(log, "Two-bore bracket")


def test_a_session_still_waiting_for_its_title_asks_again(panel):
    """A Refusal on the first Intent leaves the retry open for the next one."""
    index, _ = panel.chat_sessions.add_new_chat("New Chat")
    panel.chat_sessions.begin(index, "the truncated intent")
    log = panel.chat_sessions.get_session_info(index)["log"]

    assert panel.chat_sessions.wants_a_title(index)
    panel.chat_sessions.title_arrived(log, "Two-bore bracket")
    assert not panel.chat_sessions.wants_a_title(index)


def test_a_launch_opens_an_empty_session_and_keeps_the_earlier_ones(sessions_are_the_tests_own):
    """
    The panel used to reopen the last Session with its transcript. FCC-42.
    """
    from ui.copilot_panel import CopilotPanel
    from utils import settings_manager

    first = CopilotPanel()
    try:
        first.chat_sessions.begin(first.chat_sessions.stack.currentIndex(), "yesterday")
    finally:
        first._cleanup_thread()

    relaunched = CopilotPanel()
    try:
        live = relaunched.chat_sessions.stack.currentIndex()
        assert not relaunched.chat_sessions.get_session_info(live)["indexed"]
        assert relaunched.chat_sessions.count() >= 2, "the earlier Session was dropped"
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
    monkeypatch.setattr(panel, "_ask_for_a_title", lambda index, intent: asked.append(intent))
    panel.chip_bar.get_and_clear()
    panel.new_chat()
    index = panel.chat_sessions.stack.currentIndex()

    panel.user_input.setPlainText(
        "Model a spur gear with 24 teeth, module 2, 10mm thick, and a 12mm bore")
    panel.request_work.connect(lambda *_: None)
    try:
        panel.on_send()
    finally:
        panel.request_work.disconnect()
        panel.worker.stop()
        panel._set_running(False)

    name = panel.chat_sessions.get_session_info(index)["name"]
    assert name.startswith("Model a spur gear")
    assert panel.title_label.text() == name
    assert asked, "the generated title was never asked for"


def _widgets_in(panel):
    """What the active Session's chat area is currently showing, in order."""
    area = panel.chat_sessions.get_current_chat_history()
    layout = area.widget().layout()
    return [layout.itemAt(i).widget() for i in range(layout.count())
            if layout.itemAt(i).widget() is not None]


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
    from core.events import APPLIED, PLAN, REVIEW_VERDICT, THOUGHT, EventStream

    stream = EventStream()
    stream.log_to(path)
    stream.emit(THOUGHT, phase="plan", text="a box, then a fillet")
    stream.emit(PLAN, steps=["Make a box", "Fillet it"])
    stream.emit(APPLIED, step=1, label="Box", created=["Box"])
    stream.emit(REVIEW_VERDICT, verdict="pass", reason="Looks like a box.")


def test_a_session_comes_back_with_its_whole_transcript(panel, tmp_path):
    """
    Restoring replays the log through the switch that rendered it live, so a
    restored Session cannot drift from a live one. FCC-5.
    """
    from ui.accordion_widget import AccordionWidget
    from ui.plan_widget import PlanWidget

    log = str(tmp_path / "yesterday.jsonl")
    _a_transcript(log)
    index, _ = panel.chat_sessions.add_new_chat("yesterday")
    panel.chat_sessions.get_session_info(index)["log"] = log
    panel.chat_sessions.get_session_info(index)["restored"] = False

    panel.switch_session(index)

    kinds = [type(w).__name__ for w in _widgets_in(panel)]
    assert any(isinstance(w, PlanWidget) for w in _widgets_in(panel)), kinds
    assert any(isinstance(w, AccordionWidget) for w in _widgets_in(panel)), kinds
    said = _said_in(panel)
    assert "Applied" in said and "Box" in said
    assert "Review: pass" in said


def _a_queue_transcript(path):
    """
    A full run's structural events, in the order `core.loop._run` emits
    them: Intent, an approved Gate, one Step through to Applied, Assertion,
    Review -- everything FCC-172's Queue widget reads a phase transition off.
    """
    from core.events import (APPLIED, ASSERTION, EventStream, GATE, INTENT, PLAN,
                             REVIEW_VERDICT, STEP_STARTED)

    stream = EventStream()
    stream.log_to(path)
    stream.emit(INTENT, text="a box, then a fillet")
    stream.emit(PLAN, steps=["Make a box"])
    stream.emit(GATE, decision="approve", standing=False)
    stream.emit(STEP_STARTED, index=1, total=1, description="Make a box")
    stream.emit(APPLIED, step=1, label="Box", created=["Box"])
    stream.emit(ASSERTION, ok=True, code="assert True", confirmed=["It is a box"],
               uncovered=[], traceback="")
    stream.emit(REVIEW_VERDICT, verdict="pass", reason="Looks like a box.")


def test_a_session_comes_back_with_its_queue_at_every_phase_done(panel, tmp_path):
    """
    The Queue is restored the same way the Plan and the transcript are: by
    replaying the log through `on_event`, the one switch that also renders it
    live (FCC-172).
    """
    from ui.queue_widget import ASSERTION, DESIGN, DONE, EXECUTE, PLAN, REVIEW

    log = str(tmp_path / "queue-run.jsonl")
    _a_queue_transcript(log)
    index, _ = panel.chat_sessions.add_new_chat("queue-run")
    panel.chat_sessions.get_session_info(index)["log"] = log
    panel.chat_sessions.get_session_info(index)["restored"] = False

    panel.switch_session(index)

    queue = panel._queue_widget
    assert queue is not None
    for phase in (DESIGN, PLAN, EXECUTE, ASSERTION, REVIEW):
        assert queue.phase_state(phase) == DONE, phase
    assert queue.step_count() == 1
    assert queue.step_state(1) == DONE


def test_a_new_intent_opens_a_fresh_queue_running_design(panel, tmp_path):
    from core.events import EventStream, INTENT
    from ui.queue_widget import DESIGN, RUNNING

    log = str(tmp_path / "fresh-run.jsonl")
    stream = EventStream()
    stream.log_to(log)
    stream.emit(INTENT, text="a plain box")
    index, _ = panel.chat_sessions.add_new_chat("fresh-run")
    panel.chat_sessions.get_session_info(index)["log"] = log
    panel.chat_sessions.get_session_info(index)["restored"] = False

    panel.switch_session(index)

    queue = panel._queue_widget
    assert queue is not None
    assert queue.phase_state(DESIGN) == RUNNING


def test_a_run_blocked_in_plan_marks_the_queue_plan_failed(panel, tmp_path):
    from core.events import BLOCKED, EventStream, INTENT, PLAN
    from ui.queue_widget import EXECUTE, FAILED, PLAN as QUEUE_PLAN, QUEUED

    log = str(tmp_path / "blocked-run.jsonl")
    stream = EventStream()
    stream.log_to(log)
    stream.emit(INTENT, text="an impossible box")
    stream.emit(PLAN, steps=["Make a box"])
    stream.emit(BLOCKED, reason="You rejected the Plan.", tried=[], provider_said="")
    index, _ = panel.chat_sessions.add_new_chat("blocked-run")
    panel.chat_sessions.get_session_info(index)["log"] = log
    panel.chat_sessions.get_session_info(index)["restored"] = False

    panel.switch_session(index)

    queue = panel._queue_widget
    assert queue is not None
    assert queue.phase_state(QUEUE_PLAN) == FAILED
    assert queue.phase_state(EXECUTE) == QUEUED


def _a_cost_transcript(path):
    """Two calls, Design then Plan (FCC-176): the total after both is their sum."""
    from core.events import COST, EventStream, INTENT

    stream = EventStream()
    stream.log_to(path)
    stream.emit(INTENT, text="a box")
    stream.emit(COST, phase="design", usd=0.04, model="sonnet")
    stream.emit(COST, phase="plan", usd=0.09, model="sonnet")


def test_a_new_chat_shows_the_zero_state(panel):
    panel.new_chat()

    assert panel.usage_label.text() == "$0.00"


def test_cost_events_accumulate_into_one_running_total(panel):
    from core.events import COST, Event

    panel.new_chat()

    panel.on_event(Event(COST, {"phase": "design", "usd": 0.04, "model": "sonnet"}))
    assert panel.usage_label.text() == "$0.04"

    panel.on_event(Event(COST, {"phase": "plan", "usd": 0.07, "model": "sonnet"}))
    assert panel.usage_label.text() == "$0.11"


def test_a_call_under_a_cent_still_shows_what_it_cost(panel):
    from core.events import COST, Event

    panel.new_chat()

    panel.on_event(Event(COST, {"phase": "design", "usd": 0.0049, "model": "sonnet"}))

    assert panel.usage_label.text() == "$0.0049"


def test_a_new_intent_zeroes_the_total_mid_session(panel):
    """
    A follow-up Intent in the same open chat starts a second run, and the
    total is the run's, not the Session's (Solution Notes: "cumulative cost
    for the entire run from Intent to Review verdict") -- the same boundary
    `_update_queue` already resets `_queue_widget` on (FCC-172).
    """
    from core.events import COST, INTENT, Event

    panel.new_chat()
    panel.on_event(Event(COST, {"phase": "design", "usd": 0.04, "model": "sonnet"}))
    assert panel.usage_label.text() == "$0.04"

    panel.on_event(Event(INTENT, {"text": "a second box"}))

    assert panel.usage_label.text() == "$0.00"


def test_a_restored_session_shows_its_final_total(panel, tmp_path):
    log = str(tmp_path / "cost-run.jsonl")
    _a_cost_transcript(log)
    index, _ = panel.chat_sessions.add_new_chat("cost-run")
    panel.chat_sessions.get_session_info(index)["log"] = log
    panel.chat_sessions.get_session_info(index)["restored"] = False

    panel.switch_session(index)

    assert panel.usage_label.text() == "$0.13"


def test_a_session_recorded_before_the_step_render_went_away_still_opens(panel, tmp_path):
    """
    An old transcript carries `render` events, and this build has no such kind
    any more (FCC-81). The renderer skips it the way it skips any kind it does
    not know: a Session is not refused over an event that has been retired.
    """
    log = str(tmp_path / "before.jsonl")
    _a_transcript(log)
    with open(log, "a", encoding="utf-8") as handle:
        handle.write('{"kind": "render", "at": 1.0, '
                     '"data": {"path": "/tmp/copilot_step_1.png", "step": 1}}\n')
    index, _ = panel.chat_sessions.add_new_chat("before")
    panel.chat_sessions.get_session_info(index).update({"log": log, "restored": False})

    panel.switch_session(index)

    assert "Review: pass" in _said_in(panel), "the transcript did not replay"


def test_a_session_is_replayed_once_however_often_it_is_opened(panel, tmp_path):
    log = str(tmp_path / "twice.jsonl")
    _a_transcript(log)
    index, _ = panel.chat_sessions.add_new_chat("twice")
    panel.chat_sessions.get_session_info(index).update({"log": log, "restored": False})

    panel.switch_session(index)
    first = len(_widgets_in(panel))
    panel.switch_session(index)

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
    pass on the GUI thread, and the panel was frozen for the duration. FCC-16.
    """
    from ui import copilot_panel

    log = str(tmp_path / "long.jsonl")
    _a_long_transcript(log, 120)
    index, _ = panel.chat_sessions.add_new_chat("long")
    panel.chat_sessions.get_session_info(index).update({"log": log, "restored": False})

    replayed = []
    real = panel.on_event
    monkeypatch.setattr(panel, "on_event", lambda event: replayed.append(event) or real(event))

    panel.switch_session(index)

    assert len(replayed) == copilot_panel.RESTORE_CHUNK, \
        "the whole transcript was replayed before the event loop got a turn"

    _spin()

    assert len(replayed) == 120, "the rest of the transcript never arrived"


def test_a_short_transcript_still_lands_in_one_go(panel, tmp_path):
    """Chunking must not make the common case wait for an event-loop turn."""
    log = str(tmp_path / "short.jsonl")
    _a_transcript(log)
    index, _ = panel.chat_sessions.add_new_chat("short")
    panel.chat_sessions.get_session_info(index).update({"log": log, "restored": False})

    panel.switch_session(index)

    assert "Review: pass" in _said_in(panel)


def test_switching_away_mid_replay_abandons_the_rest(panel, tmp_path, monkeypatch):
    """The remaining chunks must not render into whichever Session is open now."""
    log = str(tmp_path / "abandoned.jsonl")
    _a_long_transcript(log, 120)
    index, _ = panel.chat_sessions.add_new_chat("abandoned")
    panel.chat_sessions.get_session_info(index).update({"log": log, "restored": False})
    elsewhere, _ = panel.chat_sessions.add_new_chat("elsewhere")

    replayed = []
    real = panel.on_event
    monkeypatch.setattr(panel, "on_event", lambda event: replayed.append(event) or real(event))

    panel.switch_session(index)
    panel.switch_session(elsewhere)
    _spin()

    from ui import copilot_panel
    assert len(replayed) == copilot_panel.RESTORE_CHUNK
    assert _widgets_in(panel) == [], "another Session's transcript landed here"


def test_a_truncated_transcript_shows_what_survived_and_says_so(panel, tmp_path):
    log = str(tmp_path / "crashed.jsonl")
    _a_transcript(log)
    with open(log, "a", encoding="utf-8") as handle:
        handle.write('{"kind": "blocked", "at": 1.0, "da')
    index, _ = panel.chat_sessions.add_new_chat("crashed")
    panel.chat_sessions.get_session_info(index).update({"log": log, "restored": False})

    panel.switch_session(index)

    said = _said_in(panel)
    assert "Applied" in said, "the surviving transcript was thrown away"
    assert "ends mid-event" in said


def test_an_event_kind_the_panel_does_not_know_is_skipped_not_fatal(panel, tmp_path):
    log = str(tmp_path / "from_the_future.jsonl")
    _a_transcript(log)
    with open(log, "a", encoding="utf-8") as handle:
        handle.write('{"kind": "almost_applied", "at": 1.0, "data": {"step": 1}}\n')
    index, _ = panel.chat_sessions.add_new_chat("future")
    panel.chat_sessions.get_session_info(index).update({"log": log, "restored": False})

    panel.switch_session(index)

    said = _said_in(panel)
    assert "Review: pass" in said


def test_a_rename_persists_and_reaches_the_header(panel):
    """
    Only a Session that has had an Intent is in the index at all, and the
    index no longer lines up positionally with the open Sessions.
    """
    from utils import settings_manager

    index, _ = panel.chat_sessions.add_new_chat("New Chat")
    panel.chat_sessions.switch_to(index)
    panel.chat_sessions.begin(index, "a first intent")
    log = panel.chat_sessions.get_session_info(index)["log"]

    panel.chat_sessions.rename_session(index, "bracket revision")
    panel.update_header_title(index)

    assert panel.title_label.text() == "bracket revision"
    stored = {s["log"]: s["name"] for s in settings_manager.instance().get_sessions()}
    assert stored[log] == "bracket revision"


def test_deleting_a_session_takes_its_transcript_with_it(panel, tmp_path):
    from utils import settings_manager

    log = str(tmp_path / "doomed.jsonl")
    _a_transcript(log)
    index, _ = panel.chat_sessions.add_new_chat("doomed")
    panel.chat_sessions.get_session_info(index)["log"] = log

    panel.chat_sessions.remove_session(index)

    assert not os.path.exists(log), "delete hid the Session and kept the transcript"
    assert "doomed" not in [s["name"] for s in settings_manager.instance().get_sessions()]


def test_pruning_takes_the_chat_area_with_the_session(sessions_are_the_tests_own,
                                                      monkeypatch):
    """A page left behind would show the wrong Session's transcript (FCC-17)."""
    from core import sessions as sessions_module
    from ui import multi_tab_manager

    monkeypatch.setattr(sessions_module, "KEEP", 3)
    manager = multi_tab_manager.ChatSessionManager()
    for n in range(5):
        index, _ = manager.add_new_chat("Chat %d" % n)
        manager.begin(index, "Chat %d" % n)

    assert manager.count() == 3
    assert manager.stack.count() == 3, "a page outlived the Session it was showing"
    assert manager.get_session_info(manager.stack.currentIndex())["name"] == "Chat 4"


def test_a_restored_session_owns_nothing(panel, tmp_path):
    """
    Each Session's Owned Objects live on its own worker, not one shared one
    (FCC-181). Reopening yesterday's Session must not grant the assistant
    rights over objects another Session owns, and switching away from a
    Session that does own something must not cost it that ownership either —
    it is not reset on every switch any more, because the pipeline switching
    away from is no longer the pipeline switching in.
    """
    from core.apply import ApplyPipeline

    live = panel.chat_sessions.stack.currentIndex()
    panel.worker.pipeline = ApplyPipeline(owned={"BoxFromTheLiveSession"})

    log = str(tmp_path / "old.jsonl")
    _a_transcript(log)
    index, _ = panel.chat_sessions.add_new_chat("old")
    panel.chat_sessions.get_session_info(index).update({"log": log, "restored": False})

    panel.switch_session(index)
    panel.worker._get_tools()
    assert panel.worker.pipeline.owned == set(), \
        "the next Step would apply against another Session's Owned Objects"

    panel.switch_session(live)
    assert panel.worker.pipeline.owned == {"BoxFromTheLiveSession"}, \
        "switching away and back cost the live Session its own Owned Objects"


def test_a_new_session_owns_nothing(panel):
    from core.apply import ApplyPipeline

    panel.worker.pipeline = ApplyPipeline(owned={"BoxFromAnotherSession"})

    panel.new_chat()

    panel.worker._get_tools()
    assert panel.worker.pipeline.owned == set()


def test_returning_to_a_session_does_not_keep_another_ones_widgets(panel, tmp_path):
    """
    Restoring is skipped the second time a Session is opened, but the render
    state still has to let go of the Session being left.
    """
    log = str(tmp_path / "first.jsonl")
    _a_transcript(log)
    first, _ = panel.chat_sessions.add_new_chat("first")
    panel.chat_sessions.get_session_info(first).update({"log": log, "restored": False})
    panel.new_chat()
    second = panel.chat_sessions.stack.currentIndex()

    panel.switch_session(first)
    plan_of_first = panel._plan_widget
    assert plan_of_first is not None, "the fixture transcript rendered no Plan"

    panel.switch_session(second)

    assert panel._plan_widget is not plan_of_first, \
        "a Step would mark the Plan of the Session the user left"
    assert panel._empty_state is None or \
        panel._empty_state.parent() is not None, "empty state points at a dead widget"


def test_switching_mid_run_moves_the_panel_without_touching_the_run(panel):
    """
    A run narrates into the Session it started from, but the Session it
    started from is no longer the only one the panel can show (FCC-181): each
    Session runs on its own worker, so leaving one running and looking at
    another is switching, not interrupting.
    """
    first, _ = panel.chat_sessions.add_new_chat("running")
    second, _ = panel.chat_sessions.add_new_chat("elsewhere")
    panel.chat_sessions.switch_to(first)
    first_bundle = panel._worker_for(panel.chat_sessions.get_session_info(first)["log"])
    first_bundle.running = True
    panel._set_running(True)

    panel.switch_session(second)

    assert panel.chat_sessions.stack.currentIndex() == second, \
        "a run in another Session refused a switch that no longer touches it"
    assert first_bundle.running, "switching away stopped a run it did not start"
    first_bundle.running = False


def test_activating_a_documents_own_session_switches_the_panel_to_it(panel):
    """
    Bringing a Document's MDI tab forward in FreeCAD moves the panel to
    whichever Session named that Document, without the user touching the
    picker (FCC-181, AC).
    """
    import FreeCAD

    doc = FreeCAD.newDocument("FCC181PanelFollowsMDI")
    try:
        elsewhere, _ = panel.chat_sessions.add_new_chat("elsewhere")
        named, _ = panel.chat_sessions.add_new_chat("named")
        panel.chat_sessions.name_document(
            panel.chat_sessions.get_session_info(named)["log"], doc.Name)
        panel.chat_sessions.switch_to(elsewhere)

        panel.slotActivateDocument(doc)

        assert panel.chat_sessions.stack.currentIndex() == named, \
            "activating the Document did not bring its own Session forward"
    finally:
        FreeCAD.closeDocument(doc.Name)


def test_activating_an_unclaimed_document_leaves_the_panel_alone(panel):
    """A Document nothing has been sent to yet has no Session to switch to."""
    import FreeCAD

    doc = FreeCAD.newDocument("FCC181PanelIgnoresUnclaimedDoc")
    try:
        index, _ = panel.chat_sessions.add_new_chat("current")
        panel.chat_sessions.switch_to(index)

        panel.slotActivateDocument(doc)

        assert panel.chat_sessions.stack.currentIndex() == index
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


def test_an_answer_typed_in_the_composer_reaches_the_loop(panel, monkeypatch):
    answered = []
    monkeypatch.setattr(panel.ask, "answer", answered.append)

    panel.on_missing_values("How many teeth?")
    QT_APP.processEvents()
    panel.user_input.setPlainText("20 teeth, module 2")
    _button(panel._question_card, "Send").click()
    QT_APP.processEvents()

    assert answered == ["20 teeth, module 2"]
    assert panel.user_input.toPlainText() == "", "the composer kept the answer"
    assert "20 teeth, module 2" in _said_in(panel), "the answer is not in the transcript"


def test_an_empty_answer_does_not_start_the_run_on_nothing(panel, monkeypatch):
    answered = []
    monkeypatch.setattr(panel.ask, "answer", answered.append)

    panel.on_missing_values("How many teeth?")
    QT_APP.processEvents()
    panel.user_input.clear()
    _button(panel._question_card, "Send").click()
    QT_APP.processEvents()

    assert answered == [], "an empty composer was sent as the answer"
    assert panel._question_card is not None, "the card went away without being answered"

    _button(panel._question_card, "You decide").click()
    QT_APP.processEvents()
    assert answered == [""], "'You decide' did not decline for the user"


def test_enter_while_a_question_is_open_answers_it_rather_than_starting_a_run(panel, monkeypatch):
    """
    Enter in the composer goes to `on_send`, which had exactly one behaviour: a
    new Intent, a new run, a new Plan. The question stayed on screen unanswered
    and the re-plan asked it again — which is what "it planned first and only
    then started asking" looked like from the outside. FCC-71.
    """
    answered, sent = [], []
    monkeypatch.setattr(panel.ask, "answer", answered.append)
    panel.request_work.connect(lambda intent, files, log: sent.append(intent))
    try:
        panel.on_missing_values("How many teeth?")
        QT_APP.processEvents()
        panel.user_input.setPlainText("20 teeth, module 2")
        panel.on_send()
        QT_APP.processEvents()
    finally:
        panel.request_work.disconnect()

    assert answered == ["20 teeth, module 2"]
    assert sent == [], "Enter started a second run instead of answering the question"
    assert panel._question_card is None, "the card is still open after being answered"
    assert panel.user_input.toPlainText() == "", "the composer kept the answer"


def test_stopping_releases_a_waiting_question(panel):
    panel.on_missing_values("How many teeth?")
    QT_APP.processEvents()
    card = panel._question_card

    panel.on_stop()
    QT_APP.processEvents()

    assert panel._question_card is None
    assert not card.isEnabled(), "the card is still answerable after Stop"


def test_the_acceptance_list_is_on_the_plan_card_before_the_gate(panel):
    """
    A criterion the user cannot see is a criterion they cannot correct, and the
    Plan Gate is the only place they could. The `plan` event is emitted before
    `gate(...)` is called, so this is what the Gate is looking at. FCC-75.
    """
    from core.events import Event, PLAN, PLAN_REVISED

    panel.on_event(Event(PLAN, {"steps": ["Sketch the hex profile", "Pad it 6.5 mm"],
                                "acceptance": ["13.0 mm across the flats", "6.5 mm tall"]}))
    QT_APP.processEvents()

    said = _said_in(panel)
    assert "13.0 mm across the flats" in said
    assert "6.5 mm tall" in said

    panel.on_event(Event(PLAN_REVISED, {"reason": "you asked for M10",
                                        "steps": ["Sketch the hex profile"],
                                        "acceptance": ["17.0 mm across the flats"]}))
    QT_APP.processEvents()

    assert "17.0 mm across the flats" in _said_in(panel)


def test_an_answer_off_the_drawing_says_so_rather_than_no_values_given(panel):
    """
    A question put back to the sheet is answered without the user, and the
    transcript has to show that rather than falling through to "No values
    given, so the Plan will say what it assumed" — which would be a lie: a
    value read off the drawing is read, not assumed. FCC-90.
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
    them, so the user could not see what the run had been shown. FCC-88.
    """
    from conftest import QtWidgets

    from core.events import ATTACHED, Event

    drawing = tmp_path / "bracket.png"
    _write_a_png(str(drawing))

    panel.on_event(Event(ATTACHED, {"files": [{"name": "bracket.png",
                                               "path": str(drawing)}]}))
    QT_APP.processEvents()

    shown = [w for w in _widgets_in(panel)
             if isinstance(w, QtWidgets.QLabel) and w.pixmap() is not None
             and not w.pixmap().isNull()]
    assert shown, "the picture is not in the transcript"
    assert shown[-1].toolTip() == "bracket.png"


def test_an_attachment_whose_file_has_gone_renders_its_name(panel):
    """
    A clipboard paste is a temp file the panel deletes when the run ends, so a
    Session opened tomorrow points at nothing. A renderer that raised would be
    swallowed by `EventStream.emit` and the whole transcript would lose it
    silently. FCC-88.
    """
    from core.events import ATTACHED, Event

    before = len(_widgets_in(panel))
    panel.on_event(Event(ATTACHED, {"files": [{"name": "pasted.png",
                                               "path": "/nowhere/pasted.png"}]}))
    QT_APP.processEvents()

    assert len(_widgets_in(panel)) == before + 1
    assert "pasted.png" in _said_in(panel)


def _write_a_png(path):
    """A file Qt itself will decode — the panel renders it through QPixmap."""
    from conftest import QtCore, QtGui

    picture = QtGui.QPixmap(8, 8)
    picture.fill(QtGui.QColor(QtCore.Qt.darkCyan))
    assert picture.save(path, "PNG")


def test_the_gate_links_to_the_document_rather_than_opening_it_in_the_chat(panel, tmp_path):
    """
    What stands above the Steps is the file the reading was written to, and the
    description itself is in the file rather than in the chat: it is the run's
    requirement, and a requirement is corrected where the run is planned from
    (FCC-108). The link is the path the `part` event carries, so what a user
    opens is that very file and not a copy of it.

    The panel is shared with the tests above, so it is the last of each that
    this one just put there.
    """
    from conftest import QtCore

    from core.design import render
    from core.events import DESIGN, PART, PLAN, Event

    from ui.chat_widgets import DocumentLinkWidget
    from ui.plan_widget import PlanWidget

    the_part = {
        "envelope": {"length": {"value": "80 mm", "flag": "read"}},
        "features": [{"id": "hole-8-left", "kind": "Ø8 through hole",
                      "position": {"the left end face": {"value": "15 mm",
                                                         "flag": "read"}}}],
    }
    document = str(tmp_path / "the-part-as-read.yaml")
    before = len(_widgets_in(panel))
    panel.on_event(Event(DESIGN, {"model": the_part}))
    panel.on_event(Event(PART, {"path": document}))
    panel.on_event(Event(PLAN, {"steps": ["Sketch the plate"], "acceptance": []}))
    QT_APP.processEvents()

    widgets = _widgets_in(panel)
    link_at, link = max((i, w) for i, w in enumerate(widgets)
                        if isinstance(w, DocumentLinkWidget))
    plan_at = max(i for i, w in enumerate(widgets) if isinstance(w, PlanWidget))

    assert link_at < plan_at, "the document linked below the Plan it was read for"
    assert QtCore.QUrl.fromLocalFile(document).toString() in link.text_label.text(), \
        "the link opens something other than the file the run wrote"
    assert link.text_label.openExternalLinks(), "clicking the link opens nothing"
    said = _said_by(widgets[before:])
    assert render(the_part) not in said
    assert "length: 80 mm (read)" not in said, "the description is in the chat"


RECORDED_GATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "logs", "sessions",
                             "fe663c12890b4d4497ad458136dfa880.jsonl")


def test_the_recorded_gate_carries_none_of_the_description_it_used_to_open(panel):
    """
    The run whose Gate was approved unread, rendered again: the description its
    two Design turns settled is not in the chat at all, and the chat is shorter
    than the description it no longer opens. Both numbers are this test's, off
    the session log.
    """
    from core.design import render
    from core.events import DESIGN, PLAN, replay

    events, _ = replay(RECORDED_GATE)
    at_the_gate = events[:1 + next(i for i, event in enumerate(events)
                                   if event.kind == PLAN)]
    described = "\n".join(render(event.data["model"]) for event in at_the_gate
                          if event.kind == DESIGN)
    assert len(described.splitlines()) > 1_000, \
        "this is no longer the run whose description filled the Gate"

    before = len(_widgets_in(panel))
    for event in at_the_gate:
        panel.on_event(event)
    QT_APP.processEvents()

    said = _said_by(_widgets_in(panel)[before:])
    assert described not in said
    assert len(said) < len(described), \
        "the Gate carries %d characters against the %d over %d lines it used to open" \
        % (len(said), len(described), len(described.splitlines()))


def test_what_was_decided_the_questions_and_the_criteria_all_stand_open(panel):
    """
    The three things the Steps are read beside. Each is short, and the Gate is
    the only place any of them is correctable, so none of them is a click away
    (ADR-0022).
    """
    from core.events import PLAN, Event

    from ui.accordion_widget import AccordionWidget
    from ui.plan_widget import PlanWidget

    before = len(_widgets_in(panel))
    panel.on_event(Event(PLAN, {
        "steps": ["Bore the ring"],
        "decided": ["envelope \u2192 thickness: 12 mm, off the section"],
        "unanswered": ["what the ring is clamped to"],
        "acceptance": ["the bore is \u00d812 through"]}))
    QT_APP.processEvents()

    beside = _widgets_in(panel)[before:]
    assert any(isinstance(widget, PlanWidget) for widget in beside)
    for heading in ("Decided for you", "Nobody answered these", "What it has to satisfy"):
        row = next(widget for widget in beside if isinstance(widget, AccordionWidget)
                   and heading in widget.toggle_button.text())
        assert row.content_area.isVisible(), "%s is a click away" % heading


def test_the_decision_card_offers_three_answers_and_returns_the_one_clicked(panel):
    """
    Take it, fix it, start again. The last is the one the Plan Gate has no word
    for: REJECT stops a run and does not licence a restart. FCC-77.
    """
    from conftest import QtWidgets

    from core.loop import START_OVER

    answered = []
    monkeypatched = panel.ask.answer
    panel.ask.answer = lambda value: (answered.append(value), monkeypatched(value))[0]

    panel.on_revision({"why": "13.0 mm across the flats", "applied": 17})
    QT_APP.processEvents()

    labels = [b.text() for b in panel._question_card.findChildren(QtWidgets.QPushButton)]
    assert len(labels) == 3, labels
    assert "17 Steps were applied" in _said_in(panel)
    assert "13.0 mm across the flats" in _said_in(panel)

    _button(panel._question_card, "Start over").click()
    QT_APP.processEvents()

    assert answered == [START_OVER]


def _review_transcript_evidence(panel, tmp_path, verdict="fail",
                                reason="the flange is mirrored"):
    """
    What `_update_review_evidence` reads off the stream before a Review
    verdict's own `on_revision` is asked anything (FCC-178): the acceptance
    checks, the four views `review_views` wrote, and the verdict itself.
    """
    from conftest import QtGui

    from core.events import ASSERTION, Event, REVIEW_VERDICT, TOOL_RESULT

    views = []
    for name in ("iso.png", "front.png", "top.png", "right.png"):
        path = str(tmp_path / name)
        QtGui.QPixmap(40, 30).save(path)
        views.append(path)

    panel.on_event(Event(ASSERTION, {"ok": True, "code": "assert True",
                                     "confirmed": ["the bore is centred"],
                                     "uncovered": [], "traceback": ""}))
    panel.on_event(Event(TOOL_RESULT, {"name": "review_views",
                                       "result": {"views": views}}))
    panel.on_event(Event(REVIEW_VERDICT, {"verdict": verdict, "reason": reason}))
    return views


def test_a_review_verdict_shows_the_confirmation_card_figma_names(panel, tmp_path):
    """
    FCC-178: once Review has run, the three-way question is Figma's
    Confirmation card -- its own evidence above Approve / Reject / Start
    Over -- not the plain question the acceptance checks fall back to when
    Review never got the chance to run at all.
    """
    from conftest import QtWidgets

    views = _review_transcript_evidence(panel, tmp_path)

    panel.on_revision({"why": "the flange is mirrored", "applied": 5})
    QT_APP.processEvents()

    card = panel._question_card
    labels = sorted(b.text() for b in card.findChildren(QtWidgets.QPushButton))
    assert labels == ["Approve", "Reject", "Start Over"], labels

    texts = [w.text() for w in card.findChildren(QtWidgets.QLabel)]
    assert any("confirmed" in text for text in texts), texts
    assert any("mirrored" in text for text in texts), texts

    pictures = [w for w in card.findChildren(QtWidgets.QLabel) if w.pixmap()]
    assert len(pictures) == len(views)

    panel._reset_render_state()


def test_clicking_approve_reject_and_start_over_on_the_verdict_card_answer_in_kind(
        panel, tmp_path):
    from conftest import QtWidgets

    from core.loop import ACCEPT, REVISE, START_OVER

    answered = []
    monkeypatched = panel.ask.answer
    panel.ask.answer = lambda value: (answered.append(value), monkeypatched(value))[0]

    for label, expected in (("Reject", REVISE), ("Start Over", START_OVER),
                            ("Approve", ACCEPT)):
        _review_transcript_evidence(panel, tmp_path)
        panel.on_revision({"why": "the flange is mirrored", "applied": 5})
        QT_APP.processEvents()

        _button(panel._question_card, label).click()
        QT_APP.processEvents()

        assert answered[-1] == expected, (label, answered)

    panel.ask.answer = monkeypatched
    panel._reset_render_state()


def test_an_assertion_failure_before_review_keeps_the_plain_decision_card(panel, tmp_path):
    """
    The acceptance checks can fail before Review is ever asked anything --
    there is no Review verdict to show, so this stays the plain question
    FCC-178 leaves alone.
    """
    from conftest import QtWidgets

    from core.events import ASSERTION, Event

    panel.on_event(Event(ASSERTION, {"ok": False, "code": "assert False",
                                     "confirmed": [], "uncovered": [],
                                     "traceback": "AssertionError", "unmet": ["the bore"]}))

    panel.on_revision({"why": "the bore is not centred", "applied": 3})
    QT_APP.processEvents()

    labels = sorted(b.text() for b in panel._question_card.findChildren(QtWidgets.QPushButton))
    assert "Approve" not in labels
    assert len(labels) == 3, labels

    panel._reset_render_state()


def test_a_new_intent_clears_the_review_verdict_that_would_have_shown_the_card(
        panel, tmp_path):
    """
    A follow-up Intent in the same Session starts a new run: a Review
    verdict left over from the run before must not turn its own decision
    card into FCC-178's, since nothing was reviewed yet this time.
    """
    from conftest import QtWidgets

    from core.events import Event, INTENT

    _review_transcript_evidence(panel, tmp_path)
    panel.on_event(Event(INTENT, {"text": "a second box"}))

    panel.on_revision({"why": "no acceptance list", "applied": 1})
    QT_APP.processEvents()

    labels = sorted(b.text() for b in panel._question_card.findChildren(QtWidgets.QPushButton))
    assert "Approve" not in labels

    panel._reset_render_state()


def test_the_plan_gate_offers_exactly_one_build_button(panel):
    """FCC-177: Build replaces Approve / Approve and carry on / Amend / Reject."""
    from conftest import QtWidgets

    from core.events import Event, PLAN

    panel.on_event(Event(PLAN, {"steps": ["Sketch the base", "Pad it"], "acceptance": []}))
    QT_APP.processEvents()
    panel.on_plan_gate(["Sketch the base", "Pad it"])
    QT_APP.processEvents()

    labels = [b.text() for b in panel._question_card.findChildren(QtWidgets.QPushButton)]
    assert labels == ["\u2713  Build"], labels


def test_clicking_build_answers_with_the_plan_as_generated_when_nothing_was_edited(
        panel, monkeypatch):
    from core.events import Event, PLAN
    from core.loop import APPROVE

    answered = []
    monkeypatch.setattr(panel.ask, "answer", answered.append)

    panel.on_event(Event(PLAN, {"steps": ["Sketch the base", "Pad it"], "acceptance": []}))
    QT_APP.processEvents()
    panel.on_plan_gate(["Sketch the base", "Pad it"])
    QT_APP.processEvents()

    _button(panel._question_card, "Build").click()
    QT_APP.processEvents()

    assert len(answered) == 1
    assert answered[0].decision == APPROVE
    assert list(answered[0].steps) == ["Sketch the base", "Pad it"]


def test_clicking_build_answers_with_the_plan_as_edited(panel, monkeypatch):
    """Editing, adding, and reordering all reach the answer Build sends. FCC-177."""
    from core.events import Event, PLAN
    from core.loop import APPROVE

    answered = []
    monkeypatch.setattr(panel.ask, "answer", answered.append)

    panel.on_event(Event(PLAN, {"steps": ["Sketch the base", "Pad it"], "acceptance": []}))
    QT_APP.processEvents()
    panel._plan_widget._list.item(1).setText("Pad it to 10mm")
    panel._plan_widget.add_step()
    panel._plan_widget._list.item(2).setText("Fillet the top edge")
    panel.on_plan_gate(["Sketch the base", "Pad it"])
    QT_APP.processEvents()

    _button(panel._question_card, "Build").click()
    QT_APP.processEvents()

    assert answered[0].decision == APPROVE
    assert list(answered[0].steps) == \
        ["Sketch the base", "Pad it to 10mm", "Fillet the top edge"]


def test_clicking_build_locks_the_plan_widget(panel, monkeypatch):
    from core.events import Event, PLAN

    monkeypatch.setattr(panel.ask, "answer", lambda value: None)

    panel.on_event(Event(PLAN, {"steps": ["Sketch the base"], "acceptance": []}))
    QT_APP.processEvents()
    plan_widget = panel._plan_widget
    panel.on_plan_gate(["Sketch the base"])
    QT_APP.processEvents()

    _button(panel._question_card, "Build").click()
    QT_APP.processEvents()

    assert plan_widget.locked


def test_stop_while_the_decision_is_open_releases_it_and_is_not_an_acceptance(panel):
    """
    `abandon` releases every waiting question with an Approval, which is not
    one of the three answers, so Stop falls to the default rather than hanging
    on an open card. Deliberately not ACCEPT: the loop asks `cancelled()`
    before it plans anything, so the run is recorded as stopped. FCC-77.
    """
    import threading

    from core.loop import ACCEPT, REVISE

    answers = []

    def waits():
        answers.append(panel.ask.about_the_revision({"why": "no", "applied": 3}))

    waiting = threading.Thread(target=waits)
    waiting.start()
    for _ in range(50):
        QT_APP.processEvents()

    panel.ask.abandon()
    waiting.join(timeout=2)

    assert not waiting.is_alive(), "Stop did not release the open decision"
    assert answers == [REVISE]
    assert answers != [ACCEPT], "Stop was recorded as the user taking the result"


def test_stop_disables_an_open_decision_card(panel):
    """A card left clickable after Stop answers a run that is already over."""
    panel.on_revision({"why": "no", "applied": 3})
    QT_APP.processEvents()
    card = panel._question_card

    panel.on_stop()
    QT_APP.processEvents()

    assert panel._question_card is None
    assert not card.isEnabled()


def test_what_was_decided_for_the_user_stands_above_the_steps(panel):
    """
    Nothing is asked between the attachment and the Gate now, so a value the
    model settled for itself is read here or nowhere. Above the Steps, because
    a decision the Steps were built on is read before them or it is not read.
    FCC-127.
    """
    from core.events import PLAN, Event

    from ui.accordion_widget import AccordionWidget
    from ui.plan_widget import PlanWidget

    panel.on_event(Event(PLAN, {
        "steps": ["Bore the ring"], "acceptance": [],
        "decided": ["features \u2192 clamp-bore \u2192 axis: perpendicular to the "
                    "plane of the upper plan, not the depth"]}))
    QT_APP.processEvents()

    widgets = _widgets_in(panel)
    decided_at, decided = max((i, w) for i, w in enumerate(widgets)
                              if isinstance(w, AccordionWidget)
                              and "Decided for you" in w.toggle_button.text())
    plan_at = max(i for i, w in enumerate(widgets) if isinstance(w, PlanWidget))

    assert decided_at < plan_at, "the decisions rendered below the Steps they were made for"
    assert decided.content_area.isVisible(), "one click away is not read"
    assert "not the depth" in _said_in(panel)


def test_reasoning_streams_inline_and_never_pins_to_the_status_line(panel):
    """
    FCC-173: the status line used to carry the last 90 characters of every
    reasoning chunk (`copilot_panel.py:1073` before this ticket). Reasoning
    now only ever lands in its own accordion, in the chat scroll.
    """
    from core.events import Event, THOUGHT

    index, _ = panel.chat_sessions.add_new_chat("reasoning-run")
    panel.switch_session(index)
    before_text, before_visible = panel.status_line.text(), panel.status_line.isVisible()

    long_thought = "the plan looks reasonable so I will start from the base sketch " * 3
    panel.on_event(Event(THOUGHT, {"phase": "plan", "text": long_thought}))
    QT_APP.processEvents()

    assert panel.status_line.text() == before_text
    assert panel.status_line.isVisible() == before_visible
    assert long_thought in _said_in(panel)


def test_reasoning_is_not_truncated_to_ninety_characters(panel):
    """The 90-character clip was the status line's; the accordion never had it."""
    from core.events import Event, THOUGHT
    from ui.accordion_widget import AccordionWidget

    index, _ = panel.chat_sessions.add_new_chat("long-reasoning-run")
    panel.switch_session(index)

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
    """A model that emits no reasoning gets no empty placeholder for it. FCC-173."""
    from core.events import Event, INTENT, PLAN
    from ui.accordion_widget import AccordionWidget

    index, _ = panel.chat_sessions.add_new_chat("no-reasoning-run")
    panel.switch_session(index)

    panel.on_event(Event(INTENT, {"text": "a plain box"}))
    panel.on_event(Event(PLAN, {"steps": ["Make a box"]}))
    QT_APP.processEvents()

    thinking = [w for w in _widgets_in(panel) if isinstance(w, AccordionWidget)
               and "Thinking" in w.toggle_button.text()]
    assert not thinking


def test_reasoning_reads_subdued_unlike_a_decision_the_plan_made(panel):
    """
    Figma's Chain of Thought component reads subdued next to an answer.
    ADR-0011 forbids a colour of its own, so this is an opacity effect on the
    reasoning accordion's text, the way `HistoryItem` dims its timestamp --
    and it is what tells reasoning apart from an accordion that is not
    reasoning, such as "Decided for you".
    """
    from core.events import Event, PLAN, THOUGHT
    from ui.accordion_widget import AccordionWidget
    from conftest import QtWidgets

    index, _ = panel.chat_sessions.add_new_chat("subdued-reasoning-run")
    panel.switch_session(index)

    panel.on_event(Event(THOUGHT, {"phase": "plan", "text": "weighing the two options"}))
    panel.on_event(Event(PLAN, {"steps": ["Make a box"], "acceptance": [],
                               "decided": ["radius → 3 mm"]}))
    QT_APP.processEvents()

    accordions = [w for w in _widgets_in(panel) if isinstance(w, AccordionWidget)]
    thinking = next(w for w in accordions if "Thinking" in w.toggle_button.text())
    decided = next(w for w in accordions if "Decided for you" in w.toggle_button.text())

    effect = thinking.content_area.graphicsEffect()
    assert isinstance(effect, QtWidgets.QGraphicsOpacityEffect)
    assert effect.opacity() < 1.0

    assert decided.content_area.graphicsEffect() is None
