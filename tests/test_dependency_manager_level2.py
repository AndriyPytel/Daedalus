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


# ── A layered install is repaired, not lived with (FCC-28) ────────────────────


def _a_distribution(target, name, version, files):
    """One installed distribution: its files, and a RECORD that claims them."""
    import os

    dist_info = os.path.join(str(target), "%s-%s.dist-info" % (name, version))
    os.makedirs(dist_info, exist_ok=True)
    for relative, text in files.items():
        path = os.path.join(str(target), relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
    with open(os.path.join(dist_info, "RECORD"), "w", encoding="utf-8") as handle:
        for relative in files:
            handle.write("%s,,\n" % relative)
        handle.write("%s-%s.dist-info/RECORD,,\n" % (name, version))
    return dist_info


def test_pip_is_told_to_upgrade_rather_than_lay_a_version_beside_the_last(
        dependency_manager, monkeypatch, tmp_path):
    """
    The cause, proven on six: without --upgrade pip declines to overwrite the
    existing file and writes the new dist-info anyway, so the metadata says
    1.16.0 while the code is still 1.15.0. That is the shape of a pydantic that
    does not match its pydantic_core.
    """
    seen = {}

    def record(command, **kwargs):
        seen["command"] = command
        return _CompletedProcess()

    monkeypatch.setattr(dependency_manager.subprocess, "run", record)

    dependency_manager.install_package("litellm", str(tmp_path))

    assert "--upgrade" in seen["command"], \
        "pip was told to install without --upgrade: %s" % seen["command"]


class _CompletedProcess:
    returncode = 0
    stdout = ""
    stderr = ""


def test_a_superseded_version_is_removed_and_the_newest_kept(dependency_manager, tmp_path):
    _a_distribution(tmp_path, "pydantic", "2.12.5",
                    {"pydantic/__init__.py": "old", "pydantic/gone_in_2_13.py": "old only"})
    _a_distribution(tmp_path, "pydantic", "2.13.4", {"pydantic/__init__.py": "new"})

    removed = dependency_manager.prune_superseded(str(tmp_path))

    assert removed == ["pydantic-2.12.5.dist-info"]
    assert list(dependency_manager.installed_distributions(str(tmp_path))["pydantic"]) \
        == [("2.13.4", str(tmp_path / "pydantic-2.13.4.dist-info"))]
    assert not (tmp_path / "pydantic-2.12.5.dist-info").exists()


def test_the_surviving_versions_own_files_are_not_deleted_with_the_old_one(
        dependency_manager, tmp_path):
    """
    --upgrade has already overwritten the shared files. Removing what the old
    RECORD claims would delete the install this is repairing.
    """
    _a_distribution(tmp_path, "pydantic", "2.12.5", {"pydantic/__init__.py": "old"})
    _a_distribution(tmp_path, "pydantic", "2.13.4", {"pydantic/__init__.py": "new"})

    dependency_manager.prune_superseded(str(tmp_path))

    assert (tmp_path / "pydantic" / "__init__.py").read_text() == "new"


def test_a_file_only_the_old_version_had_goes(dependency_manager, tmp_path):
    _a_distribution(tmp_path, "pydantic", "2.12.5",
                    {"pydantic/__init__.py": "old", "pydantic/removed.py": "stale"})
    _a_distribution(tmp_path, "pydantic", "2.13.4", {"pydantic/__init__.py": "new"})

    dependency_manager.prune_superseded(str(tmp_path))

    assert not (tmp_path / "pydantic" / "removed.py").exists(), \
        "a module the new version does not have is still importable"


def test_one_version_of_a_package_is_left_alone(dependency_manager, tmp_path):
    _a_distribution(tmp_path, "pydantic", "2.13.4", {"pydantic/__init__.py": "new"})

    assert dependency_manager.prune_superseded(str(tmp_path)) == []
    assert (tmp_path / "pydantic-2.13.4.dist-info").exists()


def test_a_distribution_is_recognised_however_its_name_is_spelled(
        dependency_manager, tmp_path):
    """`pydantic_core` and `pydantic-core` are one distribution (PEP 503)."""
    _a_distribution(tmp_path, "pydantic_core", "2.41.5", {"pydantic_core/__init__.py": "old"})
    _a_distribution(tmp_path, "pydantic-core", "2.46.4", {"pydantic_core/__init__.py": "new"})

    removed = dependency_manager.prune_superseded(str(tmp_path))

    assert removed == ["pydantic_core-2.41.5.dist-info"]


def test_versions_are_compared_as_numbers_not_as_text(dependency_manager, tmp_path):
    """2.9.0 sorts after 2.13.4 as text, and before it as a version."""
    _a_distribution(tmp_path, "pydantic", "2.9.0", {"pydantic/__init__.py": "older"})
    _a_distribution(tmp_path, "pydantic", "2.13.4", {"pydantic/__init__.py": "newer"})

    dependency_manager.prune_superseded(str(tmp_path))

    assert (tmp_path / "pydantic-2.13.4.dist-info").exists()
    assert not (tmp_path / "pydantic-2.9.0.dist-info").exists()
