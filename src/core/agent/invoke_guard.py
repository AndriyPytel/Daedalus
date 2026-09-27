"""
Claude Code's own tool-call markup, spoken as plain text instead of the
JSON protocol -- `_parsed` in `core/agent/agent.py` never answers it, so a
stream left running writes it until the call's own ceiling. `first_invoke_in`
finds the first block a stream has closed; `recovered_message` reads what it
meant, where it used one of our own protocol keys as a parameter name.
"""
import json
import re

_INVOKE = re.compile(r'<invoke\s+name="([^"]*)"\s*>(.*?)</invoke>', re.DOTALL)
_PARAMETER = re.compile(r'<parameter\s+name="([^"]*)"\s*>(.*?)</parameter>', re.DOTALL)

_PROTOCOL_KEYS = ("code", "declare", "delegate")


def first_invoke_in(text):
    """
    *text*'s first complete `<invoke>...</invoke>` block, as
    `(name, {parameter: value})`, or `None` while none has closed yet.
    """
    found = _INVOKE.search(text)
    if found is None:
        return None
    return found.group(1), dict(_PARAMETER.findall(found.group(2)))


def recovered_message(parameters):
    """
    *parameters* read back as a turn in our own protocol, or `None` where
    none of its parameter names is one we speak. `declare`'s value is the
    Step object itself, as JSON text; `code` and `delegate` carry their
    content as plain text already, the shape both take on the wire.
    """
    for key in _PROTOCOL_KEYS:
        if key not in parameters:
            continue
        if key == "declare":
            try:
                declared = json.loads(parameters["declare"])
            except ValueError:
                continue
            return json.dumps({"declare": declared})
        return json.dumps({key: parameters[key]})
    return None
