"""
The Providers page test button reached for vendor SDKs that this addon never
installs — `google.generativeai` is not a dependency, so "Test" on a Google key
died with a ModuleNotFoundError and Google looked broken. OpenAI and Anthropic
only worked by accident, off packages litellm happens to pull in.

Checked by parsing, because `utils.provider_manager` imports PySide and so is
not importable outside FreeCAD.
"""
import ast
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VENDOR_SDKS = {"openai", "anthropic", "google"}


@pytest.mark.parametrize("module", [
    os.path.join("utils", "provider_manager.py"),
    os.path.join("core", "model_catalog.py"),
])
def test_no_vendor_sdk_imports(module):
    path = os.path.join(ROOT, module)
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), path)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert not (imported & VENDOR_SDKS), (
        "%s imports undeclared vendor SDKs: %s" % (module, sorted(imported & VENDOR_SDKS))
    )


# ── the window between a key change and the list landing (FCC-53) ─────────────


@pytest.fixture
def manager():
    """
    A real _ProviderManager. Needs FreeCAD for PySide and for the settings it
    subscribes to, so this is Level 2 — the module imports neither without it.
    """
    pytest.importorskip("FreeCAD")
    from utils.provider_manager import _ProviderManager

    built = _ProviderManager()
    built._model_map = {"openai": [{"id": "gpt-4o", "display": "GPT-4o"}],
                        "anthropic": [{"id": "claude-x", "display": "Claude X"}]}
    return built


def _fetched(manager, provider, models):
    """A fetch landing, without the thread that would have carried it."""
    manager._fetched[provider] = models
    manager._fetched_with[provider] = manager._configured(provider)
    manager._remember(provider, models)


LIVE_ONLY = [{"id": "gpt-5.6-terra", "display": "GPT-5.6 Terra"}]


@pytest.mark.freecad
def test_a_fetched_only_model_still_resolves_after_a_key_change(manager):
    """
    The reported bug. `_on_keys_changed` cleared `_fetched`; `get_model_entry`
    deliberately does not fetch — it runs on the Step thread — so it fell back
    to models.json, which cannot know about a model only the provider reports.
    Changing a key and sending straight away is what a user does after topping
    up a quota. FCC-53.
    """
    _fetched(manager, "openai", LIVE_ONLY)
    assert manager.get_model_entry("gpt-5.6-terra")["provider"] == "openai"

    manager._on_keys_changed([])

    entry = manager.get_model_entry("gpt-5.6-terra")
    assert entry is not None, "the run would end in a crash report here"
    assert entry["provider"] == "openai"
    assert entry["display"] == "GPT-5.6 Terra"


@pytest.mark.freecad
def test_only_the_provider_whose_credentials_changed_is_invalidated(manager):
    """
    A key change invalidates the list that key entitled you to, and no other.
    Clearing every provider's list threw away answers that were still true.
    """
    _fetched(manager, "openai", LIVE_ONLY)
    _fetched(manager, "anthropic", [{"id": "claude-y", "display": "Claude Y"}])
    # openai's credentials are now different from the ones it fetched with.
    manager._fetched_with["openai"] = ("an-older-key", "")

    manager._on_keys_changed([])

    assert "openai" not in manager._fetched
    assert "anthropic" in manager._fetched, "an untouched provider lost its list"


@pytest.mark.freecad
def test_a_model_nobody_ever_reported_is_still_unknown(manager):
    """
    Remembering a resolution must not invent one. An id that never resolved
    stays unresolvable, and the run says so rather than guessing a provider.
    """
    assert manager.get_model_entry("no-such-model") is None
