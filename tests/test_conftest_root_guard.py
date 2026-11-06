"""Test for the session-end guard in `tests/conftest.py` that fails a run leaving new files in the checkout.

Drives a real pytest subprocess in a throwaway git repository whose conftest
loads this suite's conftest, so the hooks under test are the shipped ones and
the repository under test is not this checkout.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CONFTEST = f"""
import importlib.util
spec = importlib.util.spec_from_file_location("suite_conftest", {str(ROOT / "tests" / "conftest.py")!r})
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)
pytest_sessionstart = suite.pytest_sessionstart
pytest_sessionfinish = suite.pytest_sessionfinish
"""


def _repo(tmp_path, test_body):
    git = lambda *a: subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / ".gitignore").write_text("ignored.txt\n__pycache__/\n")
    (tmp_path / "conftest.py").write_text(CONFTEST)
    (tmp_path / "test_it.py").write_text(test_body)
    return tmp_path


def _run(repo):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "2", "test_it.py"],
        cwd=repo, capture_output=True, text=True, timeout=120,
    )


def test_a_test_that_writes_into_the_checkout_fails_the_run_and_names_the_file(tmp_path):
    repo = _repo(tmp_path, "def test_it():\n    open('stray.ifc', 'w').write('x')\n")
    result = _run(repo)
    assert result.returncode != 0
    assert "stray.ifc" in result.stdout + result.stderr
    assert (repo / "stray.ifc").exists()


def test_a_clean_run_passes(tmp_path):
    repo = _repo(tmp_path, "def test_it():\n    pass\n")
    assert _run(repo).returncode == 0


def test_a_gitignored_file_is_not_a_stray(tmp_path):
    repo = _repo(tmp_path, "def test_it():\n    open('ignored.txt', 'w').write('x')\n")
    assert _run(repo).returncode == 0


def test_a_tree_that_is_not_a_git_checkout_is_not_guarded(tmp_path):
    (tmp_path / "conftest.py").write_text(CONFTEST)
    (tmp_path / "test_it.py").write_text("def test_it():\n    open('stray.ifc', 'w').write('x')\n")
    assert _run(tmp_path).returncode == 0
