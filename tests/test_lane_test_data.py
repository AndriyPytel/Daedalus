"""lane's default test-data directory is the main checkout's, also from a git worktree."""
import os
import subprocess
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _worktree(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q")
    _git(main, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    worktree = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(worktree))
    return os.path.realpath(main), worktree


def _in_tools(code):
    code = "import sys; sys.path.insert(0, %r); %s" % (os.path.join(SRC, "tools"), code)
    out = subprocess.run([sys.executable, "-c", code], cwd=SRC, capture_output=True, text=True, check=True)
    return os.path.realpath(out.stdout.strip())


def test_the_main_checkout_of_a_worktree_is_where_the_harness_venv_lives(tmp_path):
    main, worktree = _worktree(tmp_path)
    assert _in_tools("import score; print(score.main_checkout(%r))" % str(worktree)) == main


def test_default_test_data_dir_of_a_worktree_is_the_main_checkouts(tmp_path):
    main, worktree = _worktree(tmp_path)
    code = "from tools import lane; print(lane.default_test_data_dir(%r))" % str(worktree)
    assert _in_tools(code) == os.path.join(main, "test-data")
