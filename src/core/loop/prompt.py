"""The reply-reading and attachment helpers the agent loop and its Domains share."""
import base64
import json
import mimetypes
import os
import re

from core.llm.attachments import IMAGE_SUFFIXES, PDF_SUFFIXES

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


_TRAPS_PLACEHOLDER = "{{traps}}"


_CUT_OFF_ATTEMPTS = 3


def _as_image(path):
    """
    Read a file as the content block the provider takes for it. None
    when there is nothing to show, which a sketch-only Step produces.
    The mime comes off the file rather than a stamped `image/png`, that
    once mislabelled a JPEG, and a PDF is a `file` block, not
    `image_url`, unrendered.
    """
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as handle:
        encoded = base64.b64encode(handle.read()).decode("ascii")

    mime, _ = mimetypes.guess_type(path)
    mime = mime or "application/octet-stream"
    if mime == "application/pdf":
        return {"type": "file",
                "file": {"file_data": "data:%s;base64,%s" % (mime, encoded)}}
    return {"type": "image_url",
            "image_url": {"url": "data:%s;base64,%s" % (mime, encoded)}}


def _attached(paths):
    """
    The content blocks of *paths*: an image or PDF as itself, and every other
    file in one text block by its absolute path, which the provider would not
    take but the kernel the model's code runs in can open.
    """
    shown = [p for p in paths if p.lower().endswith(IMAGE_SUFFIXES + PDF_SUFFIXES)]
    named = [os.path.abspath(p) for p in paths if p not in shown]
    blocks = [block for block in map(_as_image, shown) if block]
    if named:
        blocks.append({"type": "text", "text": "Files attached by path, for the code you run to open:\n"
                                               + "\n".join("- " + path for path in named)})
    return blocks


def _object_of(text):
    """The object a reply carries, by any of the three routes, or None."""
    fenced = _FENCE.search(text or "")
    candidate = (fenced.group(1) if fenced else (text or "")).strip()
    try:
        return json.loads(candidate)
    except ValueError:
        return _the_object_in(candidate)


def _the_object_in(text):
    """
    The first balanced `{...}` in a reply, parsed, or None.

    Balanced rather than greedy: the last `}` belongs to whatever the model
    wrote last, which on a reply ending in prose is a brace quoted in a
    sentence. Strings are stepped over so a brace inside one does not close it.
    """
    start = text.find("{")
    while start != -1:
        depth, at, in_string, escaped = 0, start, False, False
        while at < len(text):
            character = text[at]
            if in_string:
                in_string = not (character == '"' and not escaped)
                escaped = character == "\\" and not escaped
            elif character == '"':
                in_string, escaped = True, False
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:at + 1])
                    except ValueError:
                        break
            at += 1
        start = text.find("{", start + 1)
    return None
