"""
The gateway application: a FastAPI app whose `/v1` router comes from `service.api` and whose OpenAPI document is generated from code.

Starting it with `DAEDALUS_DATABASE_URL` set applies the pending migrations before it serves and runs each Session's worker as `DAEDALUS_WORKER_ARGV`, a JSON list that defaults to `python -m service.worker`.
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

WORKER_ARGV = "DAEDALUS_WORKER_ARGV"


@asynccontextmanager
async def lifespan(app):
    url = os.environ.get(db.URL_VARIABLE)
    if url is None:
        yield
        return
    argv = json.loads(os.environ.get(WORKER_ARGV, "null")) or [sys.executable, "-m", "service.worker"]
    with db.connect(url) as conn:
        db.migrate(conn)
        root = os.environ.get(ROOT_VARIABLE)
        app.state.pool = WorkerPool(argv, url, LocalBlobStore(root) if root else None)
        try:
            yield
        finally:
            app.state.pool.close_all()


app = FastAPI(title="Daedalus", version="1", lifespan=lifespan)
problem.install(app)
app.include_router(router, prefix="/v1")
app.include_router(blobs_router, prefix="/v1")
