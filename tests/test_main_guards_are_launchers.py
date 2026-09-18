"""
The only `__main__` guards under `src/` are the ones something launches: a
script whose own docstring shows it invoked as `path/to/script.py` on a
command line, or a subprocess target another module names by that same
file name. Derived from the tree, not listed by hand, so a new script with
a guard nobody wired up here fails loudly instead of just staying unlisted.
"""
import os
import re

from source_tree import sources

MAIN_GUARD = re.compile(r'^if __name__ (==|in) ', re.MULTILINE)


def _launches(name, all_sources):
    """Whether *name* (a script's own basename) appears anywhere in the tree's own text."""
    return any(name in text for _path, text in all_sources)


def test_only_launched_modules_have_a_main_guard():
    all_sources = list(sources())
    guarded = {path for path, text in all_sources if MAIN_GUARD.search(text)}
    unlaunched = [path for path in guarded if not _launches(os.path.basename(path), all_sources)]

    assert not unlaunched, (
        "a __main__ guard with nothing naming this script as its launcher "
        "reads as a dead entry point: %s" % ", ".join(unlaunched))
