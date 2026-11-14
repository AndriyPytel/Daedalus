"""
The Providers page test button reached for vendor SDKs that this addon never
installs — `google.generativeai` is not a dependency, so "Test" on a Google key
died with a ModuleNotFoundError and Google looked broken. OpenAI and Anthropic
only worked by accident, off packages litellm happens to pull in.

Checked by parsing, because `bridge.provider_manager` imports PySide and so is
not importable outside FreeCAD.
"""
import ast
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
SRC = os.path.join(ROOT, "src")
MOD = os.path.join(ROOT, "Mod")

VENDOR_SDKS = {"openai", "anthropic", "google"}


@pytest.mark.parametrize("path", [
    os.path.join(MOD, "bridge", "provider_manager.py"),
    os.path.join(SRC, "core", "model_catalog.py"),
], ids=["bridge/provider_manager.py", "core/model_catalog.py"])
def test_no_vendor_sdk_imports(path):
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


def test_no_model_list_is_bundled_with_the_addon():
    """
    Every model comes from the provider's own /models. `config/models.json` was
    a second answer to the same question that could only go stale — it still
    offered GPT-4o and Claude 3 Opus long after both were superseded, and a
    provider it did not name could not be configured at all.
    """
    assert not os.path.exists(os.path.join(ROOT, "config", "models.json"))

    with open(os.path.join(MOD, "bridge", "provider_manager.py"), encoding="utf-8") as handle:
        source = handle.read()
    assert "models.json" not in source


@pytest.fixture
def manager(monkeypatch):
    """
    A manager of its own, listening to a Settings of its own, with every fetch
    answered here rather than over the network. Needs FreeCAD for PySide, so
    this is Level 2.
    """
    pytest.importorskip("FreeCAD")
    from bridge.provider_manager import _ProviderManager
    from conftest import drain_fetches
    from core import model_catalog, settings

    asked = []

    def answer(provider, key, base_url=None):
        asked.append(provider)
        return [{"id": "gpt-x", "display": "GPT X"}] if provider == "OpenAI" else []

    monkeypatch.setattr(model_catalog, "fetch", answer)
    persisted = settings.Settings()
    with monkeypatch.context() as patch:
        patch.setattr(settings, "instance", lambda: persisted)
        built = _ProviderManager()
    persisted.set_api_keys([])
    persisted.set_base_urls({})
    built.get_available_models()
    drain_fetches(built)
    asked.clear()
    yield built, persisted, asked
    drain_fetches(built)


@pytest.mark.freecad
def test_each_landed_list_is_announced_once(manager):
    from conftest import drain_fetches

    built, persisted, asked = manager
    heard = []
    built.models_changed.connect(heard.append)

    persisted.set_api_key_for_provider("OpenAI", "sk-1")
    drain_fetches(built)

    assert asked == ["OpenAI"]
    assert [m["id"] for m in built.get_available_models() if m["provider"] == "OpenAI"] == ["gpt-x"]
    assert len(heard) == 2, "one for the key change, one for the list that landed"
