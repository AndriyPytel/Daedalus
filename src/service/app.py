"""
The gateway application: a FastAPI app whose `/v1` router comes from `service.api` and whose OpenAPI document is generated from code.

Starting it with `DAEDALUS_DATABASE_URL` set applies the pending migrations before it serves.
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from service import db
from service.api import router


@asynccontextmanager
async def lifespan(app):
    url = os.environ.get(db.URL_VARIABLE)
    if url is None:
        yield
        return
    with db.connect(url) as conn:
        db.migrate(conn)
        yield


app = FastAPI(title="Daedalus", version="1", lifespan=lifespan)
app.include_router(router, prefix="/v1")
