"""
The worker process, launched as `python -m service.worker`: the only service module that reaches the engine, and only through the public names of `sdk`; it reads `send` (a prompt, base64 attachments, for its first send the `restore` of a replaced worker, `interactive`, `confirm` and the resolved settings), `cancel` and `answer` lines from stdin while a Run is in progress and writes `ready`, `event`, `question`, `blob`, `done` and `error` lines to the real stdout.
A Run sent `interactive` asks about an Owned Object through the channel and waits for its `answer`, any other decides alone, and an Owned Object `confirm` is refused unless the Run was sent `confirm`.
After a send, cancelled or not, the STEP when the model has a solid and the FCStd checkpoint go out as `blob` lines, and anything else the engine prints goes to stderr.
"""
import base64
import json
import os
import queue
import sys
import tempfile
import threading

from sdk import Client, set_api_keys, set_base_urls, set_effort, set_last_model


AFFIRMATIVE = ("y", "yes", "ok", "true")


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


def stop_condition(cancelled, budget):
    """The callable that ends a Run: true once the Run is cancelled or its budget is spent."""
    return lambda: cancelled.is_set() or budget.exhausted()


def configure(environ):
    """Put the provider keys and base URLs the server gave this process into `sdk`, the only place the engine reads them."""
    keys = json.loads(environ.get("DAEDALUS_API_KEYS", "{}"))
    set_api_keys([{"provider": provider, "api_key": key} for provider, key in keys.items()])
    set_base_urls(json.loads(environ.get("DAEDALUS_BASE_URLS", "{}")))


def apply_settings(settings):
    """Put the resolved model and Effort of a send into `sdk`, where the run's client is built from them."""
    if settings.get("provider") and settings.get("model"):
        set_last_model(settings["provider"], settings["model"])
    if "effort" in settings:
        set_effort(settings["effort"])


def main():
    channel, sys.stdout = sys.stdout, sys.stderr
    write = threading.Lock()
    jobs, answers, cancelled = queue.Queue(), queue.Queue(), threading.Event()
    current = {"confirm": False, "interactive": False}

    def put(**message):
        with write:
            channel.write(json.dumps(message, default=str) + "\n")
            channel.flush()

    def confirm(kinds):
        if not current["interactive"]:
            return current["confirm"]
        put(op="question", text="Allow the assistant to delete %s?" % ", ".join(
            name for names in kinds.values() for name in names))
        while not cancelled.is_set():
            try:
                return answers.get(timeout=0.2).strip().lower() in AFFIRMATIVE
            except queue.Empty:
                continue
        return False

    def listen():
        for line in sys.stdin:
            try:
                message = json.loads(line)
                op = message["op"]
            except (ValueError, KeyError, TypeError):
                continue
            if op == "send":
                jobs.put(message)
            elif op == "cancel":
                cancelled.set()
            elif op == "answer":
                answers.put(str(message.get("text", "")))
        cancelled.set()
        jobs.put(None)

    configure(os.environ)
    budget = Budget()
    client = None

    def open_client(restore, directory):
        path = None
        if restore.get("checkpoint"):
            path = os.path.join(directory, "restored.FCStd")
            with open(path, "wb") as out:
                out.write(base64.b64decode(restore["checkpoint"]))
        opened = Client(document=path, confirm=confirm, history=restore.get("history") or ())
        opened.watch(budget.watch)
        opened.watch(lambda event: put(op="event", kind=event.kind, at=event.at, data=event.data))
        return opened

    put(op="ready")
    threading.Thread(target=listen, daemon=True).start()
    while True:
        job = jobs.get()
        if job is None:
            break
        answers.queue.clear()
        current["confirm"] = job.get("confirm") is True
        current["interactive"] = job.get("interactive") is True
        settings = job.get("settings") or {}
        apply_settings(settings)
        budget.start(settings.get("budget_usd"))
        with tempfile.TemporaryDirectory() as directory:
            try:
                if client is None:
                    client = open_client(job.get("restore") or {}, directory)
                paths = []
                for item in job.get("attachments", ()):
                    paths.append(os.path.join(directory, item["name"]))
                    with open(paths[-1], "wb") as out:
                        out.write(base64.b64decode(item["content"]))
                client.send(job["prompt"], attachments=paths,
                            cancelled=stop_condition(cancelled, budget))
                for kind, name, data in artifacts(client, directory):
                    put(op="blob", kind=kind, name=name, content=base64.b64encode(data).decode())
            except Exception as error:
                put(op="error", message="%s: %s" % (type(error).__name__, error))
            else:
                put(op="done")
        cancelled.clear()
    if client is not None:
        client.close()


if __name__ == "__main__":
    main()
