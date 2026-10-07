"""
Prefixed UUIDv7 ids: `ses_`, `run_`, `blob_` followed by a UUID whose leading 48 bits are the millisecond clock.

Ids made in different milliseconds sort in creation order as plain strings.
"""
import os
import time
import uuid


def new_id(prefix):
    millis = time.time_ns() // 1_000_000
    value = (millis << 80) | int.from_bytes(os.urandom(10), "big")
    value = (value & ~(0xF << 76)) | (0x7 << 76)
    value = (value & ~(0x3 << 62)) | (0x2 << 62)
    return "%s_%s" % (prefix, uuid.UUID(int=value))
