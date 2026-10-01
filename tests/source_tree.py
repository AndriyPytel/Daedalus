"""
The addon's own Python, derived rather than listed.

Three guards read this tree and refuse what they find -- a swallowed failure,
an unsanctioned route to the Document, a comment that is prose. Each held its
own tuple of package names, and each tuple was written before `sdk.py`
existed: the SDK, the one surface this addon offers, went unread by all three
for a whole epic while every one of them reported green. A list a human keeps
is a list that goes stale, so this derives it and `test_source_tree.py` fails
when the derivation and the tree disagree.

`tests` is not here: these guards judge the addon, and the suite is held to
different rules -- a test may name a Document and a test explains itself in
prose.
"""
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")


def tracked():
    """
    Every path the repository holds, relative to ROOT, from git itself.

    A guard that walks the filesystem judges whatever happens to sit in the
    working tree -- a scratch document, a local export, a directory somebody
    excluded in `.git/info/exclude` -- and fails on a file the repository
    does not ship. What the addon ships is what git tracks.
    """
    listed = subprocess.run(["git", "ls-files"], cwd=ROOT,
                            capture_output=True, text=True, check=True)
    return set(listed.stdout.split("\n")) - {""}

NOT_THE_ADDON = {"tests", "manual", "logs", "docs", "config", "src", ".git", ".venv", "harness"}


def package_dirs():
    """Every directory holding the addon's own importable Python, absolute."""
    found = []
    for parent in (SRC, ROOT):
        for name in sorted(os.listdir(parent)):
            if name in NOT_THE_ADDON or name.startswith("."):
                continue
            full = os.path.join(parent, name)
            if os.path.isdir(full) and any(
                f.endswith(".py") for _, _, files in os.walk(full) for f in files
            ):
                found.append(full)
    return found


def sources():
    """`(path relative to ROOT, source text)` for every module the addon ships."""
    for name in sorted(os.listdir(SRC)):
        full = os.path.join(SRC, name)
        if name.endswith(".py") and os.path.isfile(full):
            with open(full, encoding="utf-8") as handle:
                yield os.path.relpath(full, ROOT).replace(os.sep, "/"), handle.read()
    for directory in package_dirs():
        for path, dirnames, names in os.walk(directory):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in sorted(names):
                if not name.endswith(".py"):
                    continue
                full = os.path.join(path, name)
                with open(full, encoding="utf-8") as handle:
                    yield os.path.relpath(full, ROOT).replace(os.sep, "/"), handle.read()
