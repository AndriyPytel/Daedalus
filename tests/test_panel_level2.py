"""
The panel, built for real against FreeCAD's own PySide2, offscreen.

Most of the panel stays Level 3 — how a widget *looks* under a theme is not
something a test can tell you, which is exactly the finding behind ADR-0011.
What is checked here is the arithmetic and the state machine that would
otherwise be verified by opening the panel and squinting: the composer's
height, Send and Stop sharing one slot, and Sessions surviving a restart —
restored, renamed, deleted, and let go of when the user switches away.
"""
import os

import pytest

from conftest import QT_APP

pytestmark = pytest.mark.freecad


@pytest.fixture(scope="module")
def sessions_are_the_tests_own():
    """
    Sessions are durable now, so building a panel writes to the real settings.
    The developer's own history is not the suite's to spend.
    """
    if QT_APP is None:
        pytest.skip("PySide2 is not available")
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
        pytest.skip("PySide2 is not available")
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
    from PySide2 import QtGui

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
    from PySide2 import QtCore

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
    """
    # The dock's own minimumSizeHint only follows its child once shown, which
    # the fixture's panel is not. The child is where the width is decided.
    contents = panel.widget()
    panel.status_line.setVisible(True)
    panel.status_line.setText("Working…")
    narrow = contents.minimumSizeHint().width()

    panel.status_line.setText(status)

    assert contents.minimumSizeHint().width() == narrow, \
        "the panel's minimum width follows the status line's text"


def test_the_empty_chat_says_where_the_user_stands(panel):
    text = panel._empty_state.text()
    assert "Plan Gate" in text
    assert panel.model_combo.currentText().strip() in text


def test_the_model_picker_groups_under_provider_headings(panel):
    """A heading is a row that cannot be chosen — that is the whole mechanism."""
    from PySide2 import QtCore

    from utils import provider_manager

    if not provider_manager.instance().get_available_models():
        pytest.skip("no API key on this machine, so there is nothing to group")

    items = panel.model_combo.model()
    headings = [items.item(row) for row in range(items.rowCount())
                if not (items.item(row).flags() & QtCore.Qt.ItemIsSelectable)]

    assert headings, "the model list is flat"
    assert panel.model_combo.currentIndex() not in [items.indexFromItem(h).row()
                                                    for h in headings]


def test_the_combo_opens_on_a_stored_model_no_static_list_knows(panel, monkeypatch):
    """
    The stored id lives only in the provider's fetched list, which has not
    landed when the panel is built. Falling through to the first selectable
    row — GPT-4o — is FCC-41.
    """
    from utils import provider_manager, settings_manager

    monkeypatch.setattr(provider_manager.instance(), "awaiting_model_lists", lambda: True)
    monkeypatch.setattr(settings_manager.instance(), "get_last_model", lambda: "gpt-5.6-sol")

    panel._refresh_model_combo([{"id": "gpt-4o", "display": "GPT-4o", "provider": "OpenAI"}])

    assert panel.model_combo.currentData() == "gpt-5.6-sol"


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

    panel._refresh_model_combo([{"id": "gpt-4o", "display": "GPT-4o", "provider": "OpenAI"}])

    assert panel.model_combo.currentData() == "gpt-4o"
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

    panel._refresh_model_combo([{"id": "gpt-4o", "display": "GPT-4o", "provider": "OpenAI"}])

    assert panel.model_combo.currentData() == "gpt-4o"
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


# ── A pasted image is cleaned up on the chip's terms (FCC-15) ─────────────────


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


def test_a_finished_run_leaves_what_is_still_attached(panel, tmp_path):
    sent = _a_pasted_image(panel, tmp_path, "sent.png")
    panel.user_input.setPlainText("Build this")
    try:
        panel.on_send()
    finally:
        panel.worker.stop()
    still_attached = _a_pasted_image(panel, tmp_path, "pasted-during-the-run.png")

    panel.on_run_finished(None)

    assert not os.path.exists(sent), "the image the run consumed stayed in /tmp"
    assert os.path.exists(still_attached), "the next Intent lost the image on its chip bar"


def test_closing_the_panel_takes_the_unsent_images_with_it(panel, tmp_path):
    from PySide2 import QtGui

    left_behind = _a_pasted_image(panel, tmp_path, "never-sent.png")

    panel.closeEvent(QtGui.QCloseEvent())

    assert not os.path.exists(left_behind), "closing with an image attached leaked it"
# ── The Session picker (FCC-43) ───────────────────────────────────────────────


def _a_row(name="Bracket with two bores", document="Parts.FCStd", created_at=0.0):
    from ui.history_popup import HistoryItem

    return HistoryItem(0, name, created_at, document)


def test_the_picker_drops_from_the_top_of_the_dock_at_its_width(panel):
    """It used to open in the middle of the screen, 280px wide, detached."""
    from PySide2 import QtCore

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


def test_a_row_never_changes_height_under_the_cursor(panel):
    from PySide2 import QtCore

    row = _a_row()
    row.show()
    QT_APP.processEvents()
    resting = row.height()

    QT_APP.sendEvent(row, QtCore.QEvent(QtCore.QEvent.Enter))
    QT_APP.processEvents()

    assert row.height() == resting
# ── A Session enters history on its first Intent (FCC-42, FCC-44) ─────────────


def test_a_session_with_no_intent_is_not_in_the_history_index(panel):
    """
    Fifty promptless launches would otherwise evict fifty real Sessions:
    load() keeps the newest KEEP and deletes the log of everything past it.
    """
    from utils import settings_manager

    before = settings_manager.instance().get_sessions()

    panel.chat_sessions.add_new_chat("New Chat")

    assert settings_manager.instance().get_sessions() == before


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
    """A readable name is there from the start — never "Chat 1", never empty."""
    asked = []
    monkeypatch.setattr(panel, "_ask_for_a_title", lambda index, intent: asked.append(intent))
    # An attachment the model cannot read turns the Intent away before it names
    # anything (FCC-14), which is the behaviour — not this test's subject.
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


# ── Sessions that survive a restart (FCC-5) ───────────────────────────────────


def _widgets_in(panel):
    """What the active Session's chat area is currently showing, in order."""
    area = panel.chat_sessions.get_current_chat_history()
    layout = area.widget().layout()
    return [layout.itemAt(i).widget() for i in range(layout.count())
            if layout.itemAt(i).widget() is not None]


def _said_in(panel):
    """Every label the Session's chat area is showing, as one string."""
    from PySide2 import QtWidgets

    return " ".join(label.text()
                    for widget in _widgets_in(panel)
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
    from utils import settings_manager

    index, _ = panel.chat_sessions.add_new_chat("New Chat")
    panel.chat_sessions.switch_to(index)
    # Only a Session that has had an Intent is in the index at all (FCC-42), and
    # the index no longer lines up positionally with the open Sessions.
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


def test_the_session_bound_holds_without_a_restart(sessions_are_the_tests_own, monkeypatch, tmp_path):
    """Creating a Session prunes too, so a FreeCAD left open stays bounded. FCC-17."""
    from utils import settings_manager
    from ui import multi_tab_manager

    monkeypatch.setattr(multi_tab_manager, "KEEP", 3)
    manager = multi_tab_manager.ChatSessionManager()
    logs = []
    for n in range(5):
        index, _ = manager.add_new_chat("Chat %d" % n)
        log = str(tmp_path / ("chat%d.jsonl" % n))
        open(log, "w").close()
        manager.get_session_info(index)["log"] = log
        # Only a Session that has had an Intent is in the durable index (FCC-42).
        manager.begin(index, "Chat %d" % n)
        logs.append(log)

    assert manager.count() == 3
    assert [s["name"] for s in manager._sessions] == ["Chat 2", "Chat 3", "Chat 4"]
    # Pruning happens where the index grows — in begin() — so the live Session
    # is the one just created, at whatever position pruning left it.
    assert manager.get_session_info(manager.stack.currentIndex())["name"] == "Chat 4"
    assert not os.path.exists(logs[0]), "the pruned Session kept its transcript"
    assert os.path.exists(logs[4])
    assert len(settings_manager.instance().get_sessions()) == 3


def test_a_launch_with_no_intent_prunes_nothing(sessions_are_the_tests_own, monkeypatch, tmp_path):
    """
    FCC-17 prunes when a Session is created; FCC-42 stopped writing Sessions
    that never had an Intent. Counting those against the bound would evict a
    real Session, transcript included, on every launch where nobody typed.
    """
    from utils import settings_manager
    from ui import multi_tab_manager

    monkeypatch.setattr(multi_tab_manager, "KEEP", 3)
    manager = multi_tab_manager.ChatSessionManager()
    for n in range(3):
        index, _ = manager.add_new_chat("Real %d" % n)
        manager.begin(index, "Real %d" % n)
    kept = [s["name"] for s in settings_manager.instance().get_sessions()]

    manager.add_new_chat("New Chat")

    assert [s["name"] for s in settings_manager.instance().get_sessions()] == kept


def test_a_restored_session_owns_nothing(panel, tmp_path):
    """
    Owned Objects are the live Session's. Reopening yesterday's must not grant
    the assistant rights over objects it no longer owns.
    """
    from core.apply import ApplyPipeline

    log = str(tmp_path / "old.jsonl")
    _a_transcript(log)
    index, _ = panel.chat_sessions.add_new_chat("old")
    panel.chat_sessions.get_session_info(index).update({"log": log, "restored": False})
    panel.worker.pipeline = ApplyPipeline(owned={"BoxFromAnotherSession"})

    panel.switch_session(index)

    panel.worker._get_tools()
    assert panel.worker.pipeline.owned == set(), \
        "the next Step would apply against another Session's Owned Objects"


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


def test_switching_is_refused_while_a_run_is_in_flight(panel):
    """A run narrates into the Session it started from; moving loses track of which."""
    first, _ = panel.chat_sessions.add_new_chat("running")
    second, _ = panel.chat_sessions.add_new_chat("elsewhere")
    panel.chat_sessions.switch_to(first)
    panel._set_running(True)
    try:
        panel.switch_session(second)
        assert panel.chat_sessions.stack.currentIndex() == first
        assert "switching sessions" in panel.status_line.text()
    finally:
        panel._set_running(False)
