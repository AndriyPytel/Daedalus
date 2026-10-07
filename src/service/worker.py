"""
The worker process, launched as `python -m service.worker`: the only service module that reaches the engine, and only through the public names of `sdk`.

It reads `send` lines (a prompt, base64 attachments and the resolved settings) from stdin and writes `ready`, `event`, `blob`, `done` and `error` lines to the real stdout, ending when stdin closes.
After a send the Run's STEP, when the model has a solid, and its FCStd checkpoint go out as base64 `blob` lines; anything else the engine prints goes to stderr.
"""
import base64
import json
import os
import sys
import tempfile

from sdk import (Client, remember_model, set_api_keys, set_auto_approve_plan, set_base_urls, set_default_model, set_effort,
                 set_naming_model, set_role_table)


def artifacts(client, directory):
    """The `(kind, name, bytes)` of what a Run leaves: its STEP when the model has a solid, then its FCStd checkpoint."""
    found = []
    if client.has_solid():
        path = client.export_step(os.path.join(directory, "model.step"))
        found.append(("step", "model.step", open(path, "rb").read()))
    path = client.save(os.path.join(directory, "model.FCStd"))
    found.append(("checkpoint", "model.FCStd", open(path, "rb").read()))
    return found


class Budget:
    """The USD a Run may spend: `exhausted` turns true once the `cost` events it watched add up to the limit, and `None` is no limit."""

    def __init__(self, limit=None):
        self.start(limit)

    def start(self, limit):
        self.limit, self.spent = limit, 0.0

    def watch(self, event):
        if event.kind == "cost":
            self.spent += event.data.get("usd") or 0.0

    def exhausted(self):
        return self.limit is not None and self.spent >= self.limit


def configure(environ):
    """Put the provider keys and base URLs the server gave this process into `sdk`, the only place the engine reads them."""
    keys = json.loads(environ.get("DAEDALUS_API_KEYS", "{}"))
    set_api_keys([{"provider": provider, "api_key": key} for provider, key in keys.items()])
    set_base_urls(json.loads(environ.get("DAEDALUS_BASE_URLS", "{}")))


def apply_settings(settings):
    """Put the resolved settings of a send into `sdk`, and name each model's provider to the registry the panel's fetch fills in a GUI."""
    if "role_table" in settings:
        set_role_table(settings["role_table"])
        for row in settings["role_table"]:
            remember_model(row["model"], row["provider"])
    for key, setter in (("default_model", set_default_model), ("naming_model", set_naming_model)):
        if settings.get(key):
            setter(settings[key]["model"])
            remember_model(settings[key]["model"], settings[key]["provider"])
    if "effort" in settings:
        set_effort(settings["effort"])
    if "auto_approve_plan" in settings:
        set_auto_approve_plan(settings["auto_approve_plan"])


def main():
    channel, sys.stdout = sys.stdout, sys.stderr

    def put(**message):
        channel.write(json.dumps(message, default=str) + "\n")
        channel.flush()

    configure(os.environ)
    client = Client()
    budget = Budget()
    client.watch(budget.watch)
    applied = None
    client.watch(lambda event: put(op="event", kind=event.kind, at=event.at, data=event.data))
    put(op="ready")
    for line in sys.stdin:
        request = json.loads(line)
        settings = request.get("settings") or {}
        apply_settings(settings)
        if applied is not None and settings != applied:
            client.llm = None
        applied = settings
        budget.start(settings.get("budget_usd"))
        with tempfile.TemporaryDirectory() as directory:
            try:
                paths = []
                for item in request.get("attachments", ()):
                    paths.append(os.path.join(directory, item["name"]))
                    with open(paths[-1], "wb") as out:
                        out.write(base64.b64decode(item["content"]))
                client.send(request["prompt"], attachments=paths, cancelled=budget.exhausted)
                for kind, name, data in artifacts(client, directory):
                    put(op="blob", kind=kind, name=name, content=base64.b64encode(data).decode())
            except Exception as error:
                put(op="error", message="%s: %s" % (type(error).__name__, error))
            else:
                put(op="done")
    client.close()


if __name__ == "__main__":
    main()
