"""
The gateway application: a FastAPI app whose `/v1` router comes from `service.api` and whose OpenAPI document is generated from code.
"""
from fastapi import FastAPI

from service.api import router

app = FastAPI(title="Daedalus", version="1")
app.include_router(router, prefix="/v1")
