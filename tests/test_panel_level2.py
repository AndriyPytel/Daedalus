"""
The panel, built for real against FreeCAD's own PySide2, offscreen.

Most of the panel stays Level 3 — how a widget *looks* under a theme is not
something a test can tell you, which is exactly the finding behind ADR-0011.
What is checked here is the arithmetic and the state machine that #12 asked
for and that would otherwise be verified by opening the panel and squinting:
the composer's height, and Send and Stop sharing one slot.
"""
import pytest

from conftest import QT_APP

pytestmark = pytest.mark.freecad


@pytest.fixture(scope="module")
def panel():
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


def test_the_empty_chat_says_where_the_user_stands(panel):
    text = panel._empty_state.text()
    assert "Plan Gate" in text
    assert panel.model_combo.currentText().strip() in text


def test_the_model_picker_groups_under_provider_headings(panel):
    """A heading is a row that cannot be chosen — that is the whole mechanism."""
    from PySide2 import QtCore

    from utils.provider_manager import ProviderManager

    if not ProviderManager.instance().get_available_models():
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
    panel.request_work.connect(lambda intent, files: sent.append((intent, files)))
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
