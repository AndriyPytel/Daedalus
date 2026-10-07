"""
The one place the request's owner is decided: `current_owner` hands the bearer token to the `Authenticator` the gateway is configured with, and a request it does not recognise is 401 problem+json.

The first implementation, `BearerToken`, knows one token from `DAEDALUS_TOKEN` and the one owner `DAEDALUS_OWNER_ID` it belongs to, and compares in constant time. A gateway with no token configured answers 503 rather than letting anyone in.
"""
import hmac
import os
from typing import Optional

from fastapi import Depends, Header

from service.problem import Problem

TOKEN_VARIABLE = "DAEDALUS_TOKEN"
OWNER_VARIABLE = "DAEDALUS_OWNER_ID"


class Authenticator:
    def authenticate(self, token):
        """The owner id the bearer *token* belongs to, or `None`."""
        raise NotImplementedError


class BearerToken(Authenticator):
    def __init__(self, token, owner_id):
        self._token = token.encode()
        self._owner_id = owner_id

    def authenticate(self, token):
        return self._owner_id if hmac.compare_digest(token.encode(), self._token) else None


def authenticator():
    token = os.environ.get(TOKEN_VARIABLE)
    if not token:
        raise Problem(503, "the gateway has no bearer token configured")
    return BearerToken(token, os.environ.get(OWNER_VARIABLE, "usr_owner"))


def current_owner(authorization: Optional[str] = Header(None), auth: Authenticator = Depends(authenticator)):
    scheme, _, token = (authorization or "").partition(" ")
    owner = auth.authenticate(token) if scheme.lower() == "bearer" and token else None
    if owner is None:
        raise Problem(401, "a valid bearer token is required", headers={"WWW-Authenticate": "Bearer"})
    return owner
