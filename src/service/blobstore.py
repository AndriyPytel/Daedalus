"""
The `BlobStore` interface and its local-disk implementation, keyed like S3 so an S3-compatible store can replace it.

A key is a plain relative path of non-empty segments, such as `session/<id>/ckpt/<n>.FCStd`. `put` returns the sha256 and size of what it stored.
"""
import hashlib
import os
import tempfile
from abc import ABC, abstractmethod


def check_key(key):
    segments = key.split("/")
    if not key or key.startswith("/") or "\\" in key or any(part in ("", ".", "..") for part in segments):
        raise ValueError("not a blob key: %r" % key)


class BlobStore(ABC):
    @abstractmethod
    def put(self, key, data):
        """Store *data* under *key* and return `(sha256_hex, size)`."""

    @abstractmethod
    def get(self, key):
        """Return the bytes under *key*, or raise `KeyError`."""


class LocalBlobStore(BlobStore):
    def __init__(self, root):
        self._root = os.path.abspath(root)

    def _path(self, key):
        check_key(key)
        return os.path.join(self._root, *key.split("/"))

    def put(self, key, data):
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=os.path.dirname(path))
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(temporary, path)
        return hashlib.sha256(data).hexdigest(), len(data)

    def get(self, key):
        try:
            with open(self._path(key), "rb") as handle:
                return handle.read()
        except FileNotFoundError:
            raise KeyError(key) from None
