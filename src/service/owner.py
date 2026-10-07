"""
The owner every request acts for: `current_owner` is the one FastAPI dependency routes take.

It returns the single owner named by `DAEDALUS_OWNER_ID` until authentication replaces it.
"""
import os

OWNER_VARIABLE = "DAEDALUS_OWNER_ID"


def current_owner():
    return os.environ[OWNER_VARIABLE]
