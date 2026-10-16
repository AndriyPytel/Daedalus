"""
`BlobStore` is the contract a result store keeps, and `LocalBlobStore` is the one implementation, a directory.

Keys have the S3 shape, `session/<id>/ckpt/<n>.FCStd`, and a key that would leave the root is refused.
"""
import hashlib

import pytest

from service.blobstore import LocalBlobStore

KEY = "session/ses_1/ckpt/1.FCStd"


def test_a_put_blob_reads_back_and_put_reports_its_sha256_and_size(tmp_path):
    store = LocalBlobStore(str(tmp_path))
    data = bytes(range(256)) * 40
    sha256, size = store.put(KEY, data)
    assert store.get(KEY) == data
    assert (sha256, size) == (hashlib.sha256(data).hexdigest(), len(data))


def test_the_local_store_lays_a_key_out_as_a_path_under_its_root(tmp_path):
    LocalBlobStore(str(tmp_path)).put(KEY, b"x")
    assert (tmp_path / "session" / "ses_1" / "ckpt" / "1.FCStd").read_bytes() == b"x"


def test_a_missing_key_raises_key_error(tmp_path):
    with pytest.raises(KeyError):
        LocalBlobStore(str(tmp_path)).get("session/ses_1/ckpt/9.FCStd")


@pytest.mark.parametrize("key", ["", "/etc/passwd", "session/../../x", "session//x", "session/./x", "a\\b", "x/"])
def test_a_key_that_is_not_a_plain_relative_path_is_refused_before_anything_is_written(tmp_path, key):
    with pytest.raises(ValueError):
        LocalBlobStore(str(tmp_path)).put(key, b"x")
    assert list(tmp_path.iterdir()) == []
