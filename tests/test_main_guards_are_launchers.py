"""
The only `__main__` guards under `src/` are the ones something launches.

`kernel_worker.py`'s guard is real: the Kernel launches that file as a
subprocess. `run.py` and `lane.py` are scripts run directly. Anything else
with a `__main__` guard reads as an entry point the system does not have
(FCC-350).
"""
import re

from source_tree import sources

LAUNCHED = {
    "src/core/kernel/kernel_worker.py",
    "src/tools/run.py",
    "src/tools/lane.py",
    "src/tools/kernel_smoke.py",
    "src/tools/report.py",
}

MAIN_GUARD = re.compile(r'^if __name__ (==|in) ', re.MULTILINE)


def test_only_launched_modules_have_a_main_guard():
    guarded = {path for path, text in sources() if MAIN_GUARD.search(text)}
    assert guarded == LAUNCHED
