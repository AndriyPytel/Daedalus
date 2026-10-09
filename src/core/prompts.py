"""
Read prompt files from config/prompts/, grouped by reader in subdirectories:
`system/` (the phase and agent system prompts), `lint/` (lint refusals) and
`messages/` (everything else). Callers ask for a bare filename; this is the
one place that knows which subdirectory holds it. `LOGS` is the checkout's
log directory, resolved from this file and never from the working directory.
"""
import os

CHECKOUT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join(CHECKOUT, "config", "prompts")
LOGS = os.path.join(CHECKOUT, "logs")


def _paths():
    found = {}
    for dirpath, _dirs, names in os.walk(ROOT):
        for name in names:
            found[name] = os.path.join(dirpath, name)
    return found


def read_prompt(name):
    paths = _paths()
    if name not in paths:
        raise FileNotFoundError(name)
    with open(paths[name], encoding="utf-8") as handle:
        return handle.read()
