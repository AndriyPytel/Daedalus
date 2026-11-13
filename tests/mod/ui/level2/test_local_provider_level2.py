"""
A local server as a provider, from the Preferences page down to the combo.

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

from conftest import NO_QT, QT_APP, drain_fetches

pytestmark = pytest.mark.freecad

LOCAL = "Home server"


@pytest.fixture
def settings_are_the_tests_own():
    """The developer's own keys and addresses are not the suite's to spend."""
    if QT_APP is None:
        pytest.skip(NO_QT)
    from core import settings

    sm = settings.instance()
    their_keys, their_urls = sm.get_api_keys(), sm.get_base_urls()
    sm.set_api_keys([])
    sm.set_base_urls({})
    yield sm
    sm.set_api_keys(their_keys)
    sm.set_base_urls(their_urls)


@pytest.fixture
def providers_page(settings_are_the_tests_own):
    from ui.providers_page import ProvidersPage

    page = ProvidersPage()
    page.show()
    QT_APP.processEvents()
    _add(page, LOCAL)
    yield page
    test_thread = getattr(page, "_thread", None)
    if test_thread is not None:
        test_thread.quit()
        test_thread.wait()
    page.close()
    page.deleteLater()
    QT_APP.processEvents()


def _add(page, name):
    """Press "Add Custom Provider" and type *name* into its dialog."""
    from conftest import QtWidgets

    real = QtWidgets.QInputDialog.getText
    QtWidgets.QInputDialog.getText = staticmethod(lambda *args, **kwargs: (name, True))
    try:
        page.add_btn.click()
    finally:
        QtWidgets.QInputDialog.getText = real
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


@pytest.fixture(autouse=True)
def _fetches_finish_before_the_next_test():
    """
    Wait out this test's own fetch threads before the next test installs its
    monkeypatch on the same process-wide `provider_manager.instance()`.

    A thread still in flight lands its `fetched`/`failed` signal after a later
    test's warnings list is being recorded, and that later test fails on an
    address that is not its own.
    """
    yield
    if QT_APP is None:
        return
    from bridge import provider_manager

    drain_fetches(provider_manager.instance())


def test_no_address_means_no_fetch_and_nothing_said(settings_are_the_tests_own, monkeypatch):
    """
    The acceptance criterion the old code could not meet: with the field empty
    it started a fetch anyway, off whatever was in the key field, and printed
    "Request URL is missing an 'http://' or 'https://' protocol".
    """
    import FreeCAD
    from bridge import provider_manager
    from core import model_catalog

    manager = provider_manager.instance()
    drain_fetches(manager)

    warnings, asked = [], []
    monkeypatch.setattr(FreeCAD.Console, "PrintWarning", warnings.append)
    monkeypatch.setattr(model_catalog, "fetch",
                        lambda provider, *args, **kwargs: asked.append(provider) or [])

    manager.get_available_models()
    drain_fetches(manager)

    assert LOCAL not in asked, "a fetch was started with nothing to ask with"
    assert not any(m["provider"] == LOCAL for m in manager.get_available_models())
    assert not [line for line in warnings if LOCAL in line], \
        "an empty address was reported as a failure: %s" % warnings


def _refused(provider, api_key, base_url=None):
    raise ConnectionRefusedError("[Errno 61] Connection refused")


def test_an_unreachable_address_names_itself_and_leaves_the_rest_working(
        settings_are_the_tests_own, monkeypatch):
    """
    An unreachable server warns and names the address that could not be reached.
    The panel keeps working after the failure: asking again is still answerable.

    The fetch is faked to fail outright rather than dialled for real: a real
    connect to a closed loopback port answers on the kernel's own schedule,
    which varies enough under load to flip which of this file's tests sees
    the warning land.
    """
    import FreeCAD
    from bridge import provider_manager
    from core import model_catalog

    manager = provider_manager.instance()
    drain_fetches(manager)

    warnings = []
    monkeypatch.setattr(FreeCAD.Console, "PrintWarning", warnings.append)
    monkeypatch.setattr(model_catalog, "fetch", _refused)

    settings_are_the_tests_own.set_base_url_for_provider(LOCAL, "http://127.0.0.1:1")

    manager.get_available_models()
    drain_fetches(manager)

    assert warnings, "an unreachable server failed silently"
    assert "127.0.0.1:1" in " ".join(warnings), \
        "the warning does not name the address that could not be reached"
    assert isinstance(manager.get_available_models(), list)


def test_a_provider_added_by_name_lists_its_models_and_runs_them(
        providers_page, settings_are_the_tests_own, monkeypatch):
    """
    Add a provider, save its address and key, and its models reach the picker,
    and a run with one of them goes to that address.
    """
    from bridge import provider_manager
    from core import model_catalog
    from core.llm.factory import client_from_settings

    asked = []

    def _groq(provider, api_key, base_url=None):
        asked.append((provider, api_key, base_url))
        if provider != "Groq":
            return []
        return [{"id": "openai/gpt-oss-120b", "display": "GPT OSS 120B", "max_output": 65536}]

    monkeypatch.setattr(model_catalog, "fetch", _groq)
    manager = provider_manager.instance()
    drain_fetches(manager)

    _add(providers_page, "Groq")
    _select(providers_page, "Groq")
    providers_page.base_url_edit.setText("https://api.groq.com/openai/v1")
    providers_page.key_edit.setText("gsk-test")
    providers_page.saveSettings()
    manager.get_available_models()
    drain_fetches(manager)

    assert ("Groq", "gsk-test", "https://api.groq.com/openai/v1") in asked
    assert {"id": "openai/gpt-oss-120b", "display": "GPT OSS 120B",
            "provider": "Groq"} in manager.get_available_models()

    settings_are_the_tests_own.set_last_model("Groq", "openai/gpt-oss-120b")
    client = client_from_settings()
    assert client.model == "openai/openai/gpt-oss-120b"
    assert client.api_base == "https://api.groq.com/openai/v1"
    assert client.api_key == "gsk-test"
    assert client.output_ceiling == 65536
