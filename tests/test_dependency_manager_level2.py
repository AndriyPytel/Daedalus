"""
Level 2 — the dependency list against the imports the addon actually makes.

`core.dependency_manager` imports FreeCAD and PySide at module scope, so it
cannot be read outside a real FreeCAD the way `lint.py` can.
"""
import pytest

pytestmark = pytest.mark.freecad


@pytest.fixture
def dependency_manager():
    from core import dependency_manager

    return dependency_manager


def test_the_preflight_workers_imports_are_all_required(dependency_manager):
    assert set(dependency_manager.REQUIRED_PACKAGES) == {"litellm", "numpy", "Pillow"}


def test_a_missing_package_is_named_by_its_pip_name(dependency_manager, monkeypatch):
    monkeypatch.setattr(dependency_manager, "check_package", lambda module: False)

    assert dependency_manager.missing_packages() == ["litellm", "numpy", "Pillow"]


def test_pillow_is_looked_up_under_its_import_name(dependency_manager):
    assert dependency_manager.REQUIRED_PACKAGES["Pillow"] == "PIL"


def test_an_installed_pillow_is_found(dependency_manager):
    pytest.importorskip("PIL")

    assert dependency_manager.check_package("PIL")
    assert not dependency_manager.check_package("Pillow")
