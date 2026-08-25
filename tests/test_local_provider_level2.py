"""
A local server as a provider, from the Preferences page down to the combo (FCC-50).

The endpoint used to be typed into the password-masked "API Key" field and read
back out of it — so it was kept in secret storage, a server wanting both an
address and a token had nowhere to put the token, and the model fetch started
with a URL where a key belonged.

What is checked here is the field and what the combo does with it: the field
exists and is not masked, what it holds survives a restart, it is not in the key
list, an empty address asks nothing and an unreachable one says so.
"""
import json

import pytest

from conftest import NO_QT, QT_APP

pytestmark = pytest.mark.freecad

LOCAL = "Custom/Local"


@pytest.fixture
def settings_are_the_tests_own():
    """The developer's own keys and addresses are not the suite's to spend."""
    if QT_APP is None:
        pytest.skip(NO_QT)
    from utils import settings_manager

    sm = settings_manager.instance()
    their_keys, their_urls = sm.get_api_keys(), sm.get_base_urls()
    sm.set_api_keys([])
    sm.set_base_urls({})
    yield sm
    sm.set_api_keys(their_keys)
    sm.set_base_urls(their_urls)


@pytest.fixture
def providers_page(settings_are_the_tests_own):
    from Mod.pages.providers_page import ProvidersPage

    page = ProvidersPage()
    page.show()
    QT_APP.processEvents()
    yield page
    page.close()
    page.deleteLater()
    QT_APP.processEvents()


def _select(page, provider):
    for row in range(page.provider_list.count()):
        item = page.provider_list.item(row)
        if item.text() == provider:
            page.provider_list.setCurrentItem(item)
            QT_APP.processEvents()
            return item
    raise AssertionError("%s is not in the provider list" % provider)


def test_a_local_provider_has_an_address_field_and_it_is_not_masked(providers_page):
    from conftest import QtWidgets

    _select(providers_page, LOCAL)

    assert providers_page.base_url_container.isVisible(), \
        "there is nowhere to type the server address"
    assert providers_page.base_url_edit.echoMode() == QtWidgets.QLineEdit.Normal, \
        "the address is typed blind into a masked field"


def test_a_hosted_provider_is_not_asked_for_an_address(providers_page):
    """OpenAI's endpoint is not the user's to know, so the row is not shown."""
    _select(providers_page, "OpenAI")

    assert not providers_page.base_url_container.isVisible()


def test_a_local_providers_key_is_announced_as_optional(providers_page):
    _select(providers_page, LOCAL)
    assert "optional" in providers_page.key_lbl.text().lower()

    _select(providers_page, "OpenAI")
    assert providers_page.key_lbl.text() == "API Key"


def test_the_address_survives_and_stays_out_of_the_key_list(providers_page,
                                                            settings_are_the_tests_own):
    sm = settings_are_the_tests_own
    _select(providers_page, LOCAL)
    providers_page.base_url_edit.setText("http://localhost:11434")

    providers_page.saveSettings()

    assert sm.get_base_url_for_provider(LOCAL) == "http://localhost:11434"
    assert sm.get_api_key_for_provider(LOCAL) == "", \
        "the address was written into the API key after all"
    assert not any("11434" in json.dumps(entry) for entry in sm.get_api_keys()), \
        "the address is stored beside the secrets"


def test_a_local_server_can_have_both_an_address_and_a_token(providers_page,
                                                             settings_are_the_tests_own):
    """The failure the single field made impossible."""
    sm = settings_are_the_tests_own
    _select(providers_page, LOCAL)
    providers_page.base_url_edit.setText("http://localhost:1234")
    providers_page.key_edit.setText("sk-local")

    providers_page.saveSettings()

    assert sm.get_base_url_for_provider(LOCAL) == "http://localhost:1234"
    assert sm.get_api_key_for_provider(LOCAL) == "sk-local"


def test_an_address_typed_and_then_looked_away_from_is_not_lost(providers_page,
                                                                settings_are_the_tests_own):
    sm = settings_are_the_tests_own
    _select(providers_page, LOCAL)
    providers_page.base_url_edit.setText("http://localhost:8080")

    _select(providers_page, "OpenAI")
    _select(providers_page, LOCAL)

    assert providers_page.base_url_edit.text() == "http://localhost:8080"
    providers_page.saveSettings()
    assert sm.get_base_url_for_provider(LOCAL) == "http://localhost:8080"


def test_clearing_the_address_removes_it(providers_page, settings_are_the_tests_own):
    sm = settings_are_the_tests_own
    sm.set_base_url_for_provider(LOCAL, "http://localhost:11434")

    _select(providers_page, LOCAL)
    providers_page.base_url_edit.setText("")
    providers_page.saveSettings()

    assert sm.get_base_url_for_provider(LOCAL) == ""


def _drain(manager, timeout_turns=400):
    """
    Let every fetch already in flight finish before a test starts recording.

    The tests above save addresses nothing is listening on, so their fetches
    fail late. Clearing `_threads` hides such a thread instead of waiting for
    it, and its warning then lands in whichever test is recording at the time.
    """
    for _ in range(timeout_turns):
        QT_APP.processEvents()
        if not any(thread.isRunning() for thread in manager._threads.values()):
            return
        QT_APP.thread().msleep(5)


@pytest.fixture(autouse=True)
def _provider_manager_threads_join_before_the_next_test():
    """
    Wait out this test's own fetch threads before the next test installs its
    monkeypatch on the same process-wide `provider_manager.instance()`.

    `_threads.clear()` above drops the dict entry without joining the thread;
    a thread still in flight then lands its `fetched`/`failed` signal after a
    later test's warnings list is being recorded, and that later test fails on
    an address that is not its own.
    """
    yield
    if QT_APP is None:
        return
    from utils import provider_manager

    manager = provider_manager.instance()
    _drain(manager)
    manager._threads.clear()


def test_no_address_means_no_fetch_and_nothing_said(settings_are_the_tests_own, monkeypatch):
    """
    The acceptance criterion the old code could not meet: with the field empty
    it started a fetch anyway, off whatever was in the key field, and printed
    "Request URL is missing an 'http://' or 'https://' protocol".
    """
    import FreeCAD
    from utils import provider_manager

    manager = provider_manager.instance()
    _drain(manager)

    warnings = []
    monkeypatch.setattr(FreeCAD.Console, "PrintWarning", warnings.append)

    manager._fetched.clear()
    manager._threads.clear()

    manager.get_available_models()
    for _ in range(40):
        QT_APP.processEvents()

    assert LOCAL not in manager._threads, "a fetch was started with nothing to ask with"
    assert not any(m["provider"] == LOCAL for m in manager.get_available_models())
    assert not [line for line in warnings if LOCAL in line], \
        "an empty address was reported as a failure: %s" % warnings


def test_an_unreachable_address_names_itself_and_leaves_the_rest_working(
        settings_are_the_tests_own, monkeypatch):
    """
    An unreachable server warns and names the address that could not be reached.
    The panel keeps working after the failure: asking again is still answerable.
    """
    import FreeCAD
    from utils import provider_manager

    manager = provider_manager.instance()
    _drain(manager)

    warnings = []
    monkeypatch.setattr(FreeCAD.Console, "PrintWarning", warnings.append)

    manager._fetched.clear()
    manager._threads.clear()
    settings_are_the_tests_own.set_base_url_for_provider(LOCAL, "http://127.0.0.1:1")

    manager.get_available_models()
    for _ in range(400):
        QT_APP.processEvents()
        if warnings:
            break
        QT_APP.thread().msleep(5)

    assert warnings, "an unreachable server failed silently"
    assert "127.0.0.1:1" in " ".join(warnings), \
        "the warning does not name the address that could not be reached"
    assert isinstance(manager.get_available_models(), list)


def _naming_rows(page):
    return [(page.naming_model_combo.itemText(i), page.naming_model_combo.itemData(i))
            for i in range(page.naming_model_combo.count())]


def _choose_naming_model(page):
    """Pick the first real model — row 0 is 'Same as the chat model'."""
    for row, (_, model_id) in enumerate(_naming_rows(page)):
        if model_id:
            page.naming_model_combo.setCurrentIndex(row)
            return model_id
    pytest.skip("no models to choose from on this machine")


def test_a_naming_model_survives_the_save_that_stores_it(providers_page,
                                                         settings_are_the_tests_own):
    """
    QA against FreeCAD 1.1.3: choose a naming model, press OK, and the setting
    comes back empty. `saveSettings` wrote the API keys first, which emits
    `api_keys_changed` → `models_changed` → `_refresh_models`, rebuilding the
    combo and re-selecting it from the *stored* value — and only then read the
    combo it had just overwritten.

    The test uses an unsaved key edit so the save takes the branch that writes
    the keys.
    """
    sm = settings_are_the_tests_own
    sm.set_api_key_for_provider("OpenAI", "sk-test")
    _select(providers_page, "OpenAI")

    wanted = _choose_naming_model(providers_page)
    providers_page.key_edit.setText("sk-test-changed")

    providers_page.saveSettings()

    assert sm.get_naming_model() == wanted, \
        "the choice was destroyed by the save meant to persist it"


def test_the_default_model_survives_it_too(providers_page, settings_are_the_tests_own):
    """The same line above it, and the same failure."""
    sm = settings_are_the_tests_own
    sm.set_api_key_for_provider("OpenAI", "sk-test")
    _select(providers_page, "OpenAI")

    combo = providers_page.default_model_combo
    if combo.count() < 2:
        pytest.skip("no models to choose from on this machine")
    combo.setCurrentIndex(1)
    wanted = combo.currentData()
    providers_page.key_edit.setText("sk-test-changed")

    providers_page.saveSettings()

    assert sm.get_default_model() == wanted


def test_same_as_the_chat_model_is_a_choice_and_is_stored(providers_page,
                                                          settings_are_the_tests_own):
    """
    Empty is a decision, not an absence — it must overwrite a real model. The
    first row of the naming combo is "Same as the chat model", which is what
    stores the empty.
    """
    sm = settings_are_the_tests_own
    sm.set_api_key_for_provider("OpenAI", "sk-test")
    _select(providers_page, "OpenAI")
    _choose_naming_model(providers_page)
    providers_page.saveSettings()

    providers_page.naming_model_combo.setCurrentIndex(0)
    providers_page.saveSettings()

    assert sm.get_naming_model() == ""


def test_the_page_shows_what_was_stored_after_a_save(providers_page,
                                                     settings_are_the_tests_own):
    """The mid-save rebuild re-selected the old value; the page must not keep it."""
    sm = settings_are_the_tests_own
    sm.set_api_key_for_provider("OpenAI", "sk-test")
    _select(providers_page, "OpenAI")
    wanted = _choose_naming_model(providers_page)
    providers_page.key_edit.setText("sk-test-changed")

    providers_page.saveSettings()

    assert providers_page.naming_model_combo.currentData() == wanted
    assert sm.get_naming_model() == wanted
