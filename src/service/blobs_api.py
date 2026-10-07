"""
The blob routes: an upload takes the raw body and a `name`, a download is scoped to the current owner, and another owner's blob is 404.

The STEP and checkpoint of a Run are fetched by Run id.
"""
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from service import db
from service.blobs import store_upload
from service.blobstore import LocalBlobStore
from service.owner import current_owner
from service.repository import Repository

router = APIRouter()
ROOT_VARIABLE = "DAEDALUS_BLOB_ROOT"


class Blob(BaseModel):
    id: str
    key: str
    sha256: str
    size: int
    name: str


def blob_store():
    return LocalBlobStore(os.environ[ROOT_VARIABLE])


def repository():
    with db.connect(os.environ[db.URL_VARIABLE]) as conn:
        yield Repository(conn)


def _content(row, store):
    if row is None:
        raise HTTPException(404, "no such blob")
    try:
        data = store.get(row["key"])
    except KeyError:
        raise HTTPException(404, "no such blob") from None
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": 'attachment; filename="%s"' % row["name"]})


@router.post("/blobs", status_code=201, response_model=Blob)
async def upload(request: Request, name: str, owner=Depends(current_owner), repo=Depends(repository), store=Depends(blob_store)):
    return store_upload(repo, store, owner, name, await request.body())


@router.get("/blobs/{blob_id}")
def download(blob_id: str, owner=Depends(current_owner), repo=Depends(repository), store=Depends(blob_store)):
    return _content(repo.get_blob(owner, blob_id), store)


@router.get("/runs/{run_id}/step")
def step(run_id: str, owner=Depends(current_owner), repo=Depends(repository), store=Depends(blob_store)):
    return _content(repo.get_run_blob(owner, run_id, "step"), store)


@router.get("/runs/{run_id}/checkpoint")
def checkpoint(run_id: str, owner=Depends(current_owner), repo=Depends(repository), store=Depends(blob_store)):
    return _content(repo.get_run_blob(owner, run_id, "checkpoint"), store)
