"""
The gateway application: a FastAPI app whose `/v1` router comes from `service.api` and whose OpenAPI document is generated from code.

Starting it with `DAEDALUS_DATABASE_URL` set applies the pending migrations before it serves and runs each Session's worker as `DAEDALUS_WORKER_ARGV`, a JSON list that defaults to `python -m service.worker`.
At most `DAEDALUS_MAX_SESSIONS` (2) workers run at once, a worker idle for `DAEDALUS_IDLE_TTL` seconds (900) is stopped, and a Run left active by a gateway that stopped is failed at start.
"""
import json
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from service import db, problem
from service.api import router
from service.blobs_api import ROOT_VARIABLE, router as blobs_router
from service.blobstore import LocalBlobStore
from service.pool import WorkerPool
from service.preferences_api import router as preferences_router
from service.repository import Repository

WORKER_ARGV = "DAEDALUS_WORKER_ARGV"
MAX_SESSIONS = "DAEDALUS_MAX_SESSIONS"
IDLE_TTL = "DAEDALUS_IDLE_TTL"


@asynccontextmanager
async def lifespan(app):
    url = os.environ.get(db.URL_VARIABLE)
    if url is None:
        yield
        return
    argv = json.loads(os.environ.get(WORKER_ARGV, "null")) or [sys.executable, "-m", "service.worker"]
    with db.connect(url) as conn:
        db.migrate(conn)
        Repository(conn).fail_orphan_runs("the gateway restarted and the worker of this Run is gone")
        root = os.environ.get(ROOT_VARIABLE)
        app.state.pool = WorkerPool(argv, url, LocalBlobStore(root) if root else None,
                                    int(os.environ.get(MAX_SESSIONS, "2")), float(os.environ.get(IDLE_TTL, "900")))
        try:
            yield
        finally:
            app.state.pool.close_all()


app = FastAPI(title="Daedalus", version="1", lifespan=lifespan)
problem.install(app)
app.include_router(router, prefix="/v1")
app.include_router(blobs_router, prefix="/v1")
app.include_router(preferences_router, prefix="/v1")
