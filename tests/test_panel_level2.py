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

    index, _ = panel.chat_sessions.add_new_chat("Chat 9")
    panel.chat_sessions.switch_to(index)

    panel.chat_sessions.rename_session(index, "bracket revision")
    panel.update_header_title(index)

    assert panel.title_label.text() == "bracket revision"
    assert settings_manager.instance().get_sessions()[index]["name"] == "bracket revision"


def test_deleting_a_session_takes_its_transcript_with_it(panel, tmp_path):
    from utils import settings_manager

    log = str(tmp_path / "doomed.jsonl")
    _a_transcript(log)
    index, _ = panel.chat_sessions.add_new_chat("doomed")
    panel.chat_sessions.get_session_info(index)["log"] = log

    panel.chat_sessions.remove_session(index)

    assert not os.path.exists(log), "delete hid the Session and kept the transcript"
    assert "doomed" not in [s["name"] for s in settings_manager.instance().get_sessions()]


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
