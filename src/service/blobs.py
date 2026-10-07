"""
Writes a blob through the `BlobStore` and records its row: the file goes to the store, only key, sha256 and size go to Postgres.

A Run's STEP is keyed `session/<id>/run/<run>/model.step` and its checkpoint `session/<id>/ckpt/<n>.FCStd`; an upload is keyed `upload/<blob>/<name>`.
"""
import re

from service.ids import new_id


def safe_name(name):
    cleaned = re.sub(r"[^\w.\- ]", "_", (name or "").replace("\\", "/").rsplit("/", 1)[-1]).strip(". ")
    return cleaned or "blob"


def store_upload(repo, store, owner_id, name, data):
    blob_id, name = new_id("blob"), safe_name(name)
    key = "upload/%s/%s" % (blob_id, name)
    sha256, size = store.put(key, data)
    return repo.create_blob(owner_id, key, sha256, size, "attachment", name=name, blob_id=blob_id)


def store_result(repo, store, owner_id, session_id, run_id, kind, data):
    if kind == "step":
        key = "session/%s/run/%s/model.step" % (session_id, run_id)
    elif kind == "checkpoint":
        key = "session/%s/ckpt/%d.FCStd" % (session_id, repo.count_session_blobs(owner_id, session_id, kind) + 1)
    else:
        raise ValueError("not a result kind: %r" % kind)
    sha256, size = store.put(key, data)
    return repo.create_blob(owner_id, key, sha256, size, kind, session_id, run_id)
