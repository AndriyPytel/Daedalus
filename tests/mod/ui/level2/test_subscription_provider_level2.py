"""
The Claude subscription as a provider, from the Preferences page down to the client.

The first provider with neither a key nor a base URL: three places read "no key
and no address" as "not configured", and a fourth — the Test button — refuses to
test what it cannot see a key for. What is checked here is that the page offers
it with nothing to type, that its Test reports the account rather than fetching a
model list, that the combo carries the CLI's three aliases, and that a run on one
of them builds the client that spawns `claude` instead of the one that bills a key.

No `claude` binary is involved: `llm.signed_in` is the seam the Test button goes
through, and the model list is answered by `core/model_catalog.py` itself.
"""
import pytest

from conftest import NO_QT, QT_APP

from core.model_catalog import CLAUDE

pytestmark = pytest.mark.freecad

SIGNED_IN = (True, "Signed in as someone@example.com on a max subscription.")


@pytest.fixture
def settings_are_the_tests_own():
    """The developer's own keys and addresses are not the suite's to spend."""
    if QT_APP is None:
        pytest.skip(NO_QT)
    from bridge import settings_manager

    sm = settings_manager.instance()
    their_keys, their_urls = sm.get_api_keys(), sm.get_base_urls()
    sm.set_api_keys([])
    sm.set_base_urls({})
    yield sm
    sm.set_api_keys(their_keys)
    sm.set_base_urls(their_urls)


def _drain(manager, turns=400):
    """Let every fetch already in flight finish before a test asserts anything."""
    for _ in range(turns):
        QT_APP.processEvents()
        if not any(thread.isRunning() for thread in manager._threads.values()):
            return
        QT_APP.thread().msleep(5)


@pytest.fixture(autouse=True)
def _provider_manager_threads_join_before_the_next_test():
    """
    Wait out this test's own fetch threads before the next test installs its
    monkeypatch on the same process-wide `provider_manager.instance()`.

    A thread still in flight when a test ends lands its `fetched`/`failed`
    signal after a later test's own recording has started, and that later
    test then fails on a provider that is not its own.
    """
    yield
    if QT_APP is None:
        return
    from bridge import provider_manager

    manager = provider_manager.instance()
    _drain(manager)
    manager._threads.clear()


@pytest.fixture
def manager(settings_are_the_tests_own, monkeypatch):
    """The provider manager, with models.dev refused so the list is the aliases alone."""
    from bridge import provider_manager
    from core import model_catalog

    monkeypatch.setattr(model_catalog, "MODELS_DEV", "http://127.0.0.1:9")

    instance = provider_manager.instance()
    _drain(instance)
    instance.get_models_for_provider(CLAUDE)
    _drain(instance)
    return instance


@pytest.fixture
def providers_page(settings_are_the_tests_own, monkeypatch):
    from core import llm
    from ui.providers_page import ProvidersPage

    monkeypatch.setattr(llm, "signed_in", lambda: SIGNED_IN)
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


def _tested(page, turns=1000):
    """Press Test and wait for the worker thread to come back with what it found."""
    page._run_test()
    for _ in range(turns):
        QT_APP.processEvents()
        if page.test_btn.isEnabled():
            return page.status_lbl.text()
        QT_APP.thread().msleep(5)
    raise AssertionError("the connection test never came back")


def test_the_page_offers_the_subscription_with_nothing_to_type(providers_page):
    _select(providers_page, CLAUDE)

    assert not providers_page.key_container.isVisible(), \
        "a key field for a provider whose key nothing reads"
    assert not providers_page.base_url_container.isVisible()


def test_the_test_button_reports_the_account_and_the_plan(providers_page):
    """
    Every other provider's Test proves the key by fetching the model list.
    There is no key here to prove anything with, and the account the CLI is
    signed in as is what the button has to answer instead.
    """
    _select(providers_page, CLAUDE)

    said = _tested(providers_page)

    assert "someone@example.com" in said
    assert "max" in said


def test_a_provider_that_does_need_a_key_still_says_when_it_has_none(providers_page,
                                                                     settings_are_the_tests_own):
    """
    The refusal that stands: with the keys cleared, OpenAI has nothing to test
    with, and only the provider that needs no key skips that check.
    """
    _select(providers_page, "OpenAI")

    assert _tested(providers_page) == "No key"


def test_the_model_list_offers_the_clis_three_aliases(manager):
    assert [model["id"] for model in manager.get_models_for_provider(CLAUDE)] == \
        ["opus", "sonnet", "haiku"]


def test_the_subscription_is_offered_with_no_key_stored(manager):
    """
    `_configured` skips a provider with neither a key nor an address, so this
    one would never have been asked for its models at all.
    """
    offered = [model for model in manager.get_available_models()
               if model["provider"] == CLAUDE]

    assert [model["id"] for model in offered] == ["opus", "sonnet", "haiku"]


def test_a_model_on_the_subscription_resolves_without_a_key(manager,
                                                            settings_are_the_tests_own):
    """`get_llm_credentials` raises when a provider has neither key nor address."""
    provider, model_id, key = settings_are_the_tests_own.get_llm_credentials("sonnet")

    assert (provider, model_id, key) == (CLAUDE, "sonnet", "")


def test_the_client_for_a_subscription_model_spawns_the_cli(manager):
    """
    Not a `LiteLLMClient` with an empty key: LiteLLM has no route to a
    subscription, and the alias is not a model id it knows.
    """
    from core.llm import ClaudeCodeClient, client_from_settings

    client = client_from_settings("sonnet")

    assert isinstance(client, ClaudeCodeClient)
    assert client.model == "sonnet"


def test_the_test_button_asks_the_cli_and_never_the_network(monkeypatch, manager):
    """`claude auth status` spends no call at all, which is the whole point of it."""
    from core import llm, model_catalog

    def no_fetching(*_, **__):
        raise AssertionError("the subscription's Test button fetched a model list")

    monkeypatch.setattr(model_catalog, "fetch", no_fetching)
    monkeypatch.setattr(llm, "signed_in", lambda: SIGNED_IN)

    assert manager.test_connection(CLAUDE, "", "") == SIGNED_IN
