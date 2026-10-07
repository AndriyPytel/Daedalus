"""
The `/v1` routes, each a thin layer over the gateway.

Resources are flat and named by id; a Run is asynchronous, so `POST /v1/runs` answers 202 with the Run and its state and cost are read with `GET`.
`POST /v1/runs/{id}:cancel` and `:answer` act on a Run, a wrong state being a 409 and another owner's Run a 404; lists answer `{object: "list", data, next_cursor, has_more}` and take `limit` and `cursor`.
"""
import json
import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from service import db, events
from service.owner import current_owner
from service.preferences import Preferences, given, resolve
from service.problem import Problem
from service.repository import Repository, RunActive, SessionClosed
from service.supervisor import TERMINAL

router = APIRouter()


class Health(BaseModel):
    status: str


class NewRun(BaseModel):
    prompt: str = Field(min_length=1)
    attachments: List[str] = []
    session_id: Optional[str] = None
    overrides: Optional[Preferences] = None
    interactive: Optional[bool] = None
    confirm: Optional[bool] = None


class Answer(BaseModel):
    answer: str = Field(min_length=1)


class Run(BaseModel):
    object: str = "run"
    id: str
    session_id: str
    state: str
    interactive: bool
    confirm: bool
    cost_usd: float
    prompt: str
    attachments: list
    created_at: datetime


class Session(BaseModel):
    object: str = "session"
    id: str
    created_at: datetime
    closed_at: Optional[datetime]


class RunList(BaseModel):
    object: str = "list"
    data: List[Run]
    next_cursor: Optional[str]
    has_more: bool


class SessionList(BaseModel):
    object: str = "list"
    data: List[Session]
    next_cursor: Optional[str]
    has_more: bool


def database_url():
    url = os.environ.get(db.URL_VARIABLE)
    if url is None:
        raise Problem(503, "the gateway has no database configured")
    return url


def repository():
    conn = db.connect(database_url())
    try:
        yield Repository(conn)
    finally:
        conn.close()


def page(rows, limit, model):
    more = len(rows) > limit
    data = [model(**row) for row in rows[:limit]]
    return {"data": data, "next_cursor": data[-1].id if more else None, "has_more": more}


def limit_query():
    return Query(20, ge=1, le=100)


@router.get("/health", response_model=Health)
def health():
    return Health(status="ok")


@router.post("/runs", response_model=Run, status_code=202)
def start_run(request: Request, body: NewRun, owner: str = Depends(current_owner), repo: Repository = Depends(repository),
              key: Optional[str] = Header(None, alias="Idempotency-Key")):
    asked = given(body.overrides) if body.overrides else {}
    asked.update({name: value for name, value in (("interactive", body.interactive), ("confirm", body.confirm)) if value is not None})
    settings = resolve(repo.list_preferences(owner), asked)
    for blob_id in body.attachments:
        if repo.get_blob(owner, blob_id) is None:
            raise Problem(404, "no such blob: %s" % blob_id)
    try:
        run, created = repo.start_run(owner, body.prompt, body.attachments, body.session_id, key,
                                      settings["interactive"], settings["confirm"])
    except LookupError:
        raise Problem(404, "no such Session: %s" % body.session_id)
    except SessionClosed:
        raise Problem(409, "the Session %s is closed" % body.session_id)
    except RunActive:
        raise Problem(409, "the Session %s already has an active Run" % body.session_id)
    if not created:
        if run["prompt"] != body.prompt or body.session_id not in (None, run["session_id"]):
            raise Problem(409, "the Idempotency-Key %s was used for a different request" % key)
        return run
    request.app.state.pool.submit(owner, run["session_id"], run["id"], run["prompt"], run["attachments"],
                                  settings["interactive"], settings["confirm"], settings)
    return run


@router.get("/runs", response_model=RunList)
def list_runs(session_id: Optional[str] = None, cursor: str = "", limit: int = limit_query(),
              owner: str = Depends(current_owner), repo: Repository = Depends(repository)):
    return page(repo.list_runs(owner, session_id, cursor, limit + 1), limit, Run)


@router.get("/runs/{run_id}", response_model=Run)
def get_run(run_id: str, owner: str = Depends(current_owner), repo: Repository = Depends(repository)):
    run = repo.get_run(owner, run_id)
    if run is None:
        raise Problem(404, "no such Run: %s" % run_id)
    return run


@router.post("/runs/{run_id}:cancel", response_model=Run, status_code=202)
def cancel_run(request: Request, run_id: str, owner: str = Depends(current_owner), repo: Repository = Depends(repository)):
    run = repo.get_run(owner, run_id)
    if run is None:
        raise Problem(404, "no such Run: %s" % run_id)
    if run["state"] in TERMINAL:
        raise Problem(409, "the Run %s is already %s" % (run_id, run["state"]))
    request.app.state.pool.cancel(owner, run["session_id"], run_id)
    return repo.get_run(owner, run_id)


@router.post("/runs/{run_id}:answer", response_model=Run, status_code=202)
def answer_run(request: Request, run_id: str, body: Answer, owner: str = Depends(current_owner),
               repo: Repository = Depends(repository)):
    run = repo.get_run(owner, run_id)
    if run is None:
        raise Problem(404, "no such Run: %s" % run_id)
    answered = repo.transition(owner, run_id, "running", "needs-answer")
    if answered is None:
        raise Problem(409, "the Run %s is %s, not needs-answer" % (run_id, run["state"]))
    try:
        request.app.state.pool.answer(run["session_id"], body.answer)
    except RuntimeError as error:
        raise Problem(409, str(error))
    return answered


@router.get("/runs/{run_id}/events")
def run_events(run_id: str, owner: str = Depends(current_owner), url: str = Depends(database_url),
               last_event_id: Optional[str] = Header(None)):
    after = 0
    if last_event_id:
        if not last_event_id.isdigit():
            raise Problem(400, "Last-Event-ID %r is not an event id" % last_event_id)
        after = int(last_event_id)
    with db.connect(url) as conn:
        found = Repository(conn).get_run(owner, run_id)
    if found is None:
        raise Problem(404, "no such Run: %s" % run_id)
    return StreamingResponse(events.stream(url, owner, run_id, after),
                             media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/sessions", response_model=SessionList)
def list_sessions(cursor: str = "", limit: int = limit_query(), owner: str = Depends(current_owner),
                  repo: Repository = Depends(repository)):
    return page(repo.list_sessions(owner, cursor, limit + 1), limit, Session)


@router.get("/sessions/{session_id}", response_model=Session)
def get_session(session_id: str, owner: str = Depends(current_owner), repo: Repository = Depends(repository)):
    session = repo.get_session(owner, session_id)
    if session is None:
        raise Problem(404, "no such Session: %s" % session_id)
    return session


@router.delete("/sessions/{session_id}", response_model=Session)
def close_session(request: Request, session_id: str, owner: str = Depends(current_owner),
                  repo: Repository = Depends(repository)):
    try:
        session = repo.close_session(owner, session_id)
    except LookupError:
        raise Problem(404, "no such Session: %s" % session_id)
    request.app.state.pool.close(session_id)
    return session
