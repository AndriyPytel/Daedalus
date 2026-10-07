"""
The one place the request's owner is decided: a single configured owner until the bearer token decides it.
"""
import os


def current_owner():
    return os.environ.get("DAEDALUS_OWNER_ID", "usr_owner")
