"""
The `/v1/users/{id}/preferences` routes: GET reads the stored preferences and the settings they resolve to, PATCH merges the keys it names and removes those it sets to null.

A user id other than the caller's is 404, as another owner's Run is.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from service.api import repository
from service.owner import current_owner
from service.preferences import Preferences, given, resolve
from service.problem import Problem
from service.repository import Repository

router = APIRouter()


class PreferenceSet(BaseModel):
    object: str = "preferences"
    user_id: str
    preferences: dict
    effective: dict


def _view(repo, owner):
    stored = repo.list_preferences(owner)
    return PreferenceSet(user_id=owner, preferences=stored, effective=resolve(stored))


def _mine(user_id, owner):
    if user_id != owner:
        raise Problem(404, "no such user: %s" % user_id)


@router.get("/users/{user_id}/preferences", response_model=PreferenceSet)
def get_preferences(user_id: str, owner: str = Depends(current_owner), repo: Repository = Depends(repository)):
    _mine(user_id, owner)
    return _view(repo, owner)


@router.patch("/users/{user_id}/preferences", response_model=PreferenceSet)
def patch_preferences(user_id: str, body: Preferences, owner: str = Depends(current_owner),
                      repo: Repository = Depends(repository)):
    _mine(user_id, owner)
    repo.ensure_user(owner)
    for key, value in given(body).items():
        if value is None:
            repo.delete_preference(owner, key)
        else:
            repo.set_preference(owner, key, value)
    return _view(repo, owner)
