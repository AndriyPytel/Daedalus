"""
The worker process, launched as `python -m service.worker`: the only service module that reaches the engine, and only through `sdk.Client`; it reads `send` (a prompt, base64 attachments, `interactive`, `confirm`), `cancel` and `answer` lines from stdin while a Run is in progress and writes `ready`, `event`, `question`, `blob`, `done` and `error` lines to the real stdout.
A Run sent `interactive` asks through the channel and waits for its `answer`, any other decides alone, and an Owned Object `confirm` is refused unless the Run was sent `confirm`.
After a send, cancelled or not, the STEP when the model has a solid and the FCStd checkpoint go out as `blob` lines, and anything else the engine prints goes to stderr.
"""
import base64
import json
import os
import queue
import sys
import tempfile
import threading

from sdk import Client


def artifacts(client, directory):
    """The `(kind, name, bytes)` of what a Run leaves: its STEP when the model has a solid, then its FCStd checkpoint."""
    found = []
    if client.has_solid():
        path = client.export_step(os.path.join(directory, "model.step"))
        found.append(("step", "model.step", open(path, "rb").read()))
    path = client.save(os.path.join(directory, "model.FCStd"))
    found.append(("checkpoint", "model.FCStd", open(path, "rb").read()))
    return found


def main():
    channel, sys.stdout = sys.stdout, sys.stderr
    write = threading.Lock()
    jobs, answers, cancelled, current = queue.Queue(), queue.Queue(), threading.Event(), {"confirm": False}

    def put(**message):
        with write:
            channel.write(json.dumps(message, default=str) + "\n")
            channel.flush()

    def ask(question):
        put(op="question", text=question)
        while not cancelled.is_set():
            try:
                return answers.get(timeout=0.2)
            except queue.Empty:
                continue
        return ""

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

    client = Client(confirm=lambda kinds: current["confirm"])
    client.watch(lambda event: put(op="event", kind=event.kind, at=event.at, data=event.data))
    put(op="ready")
    threading.Thread(target=listen, daemon=True).start()
    while True:
        job = jobs.get()
        if job is None:
            break
        answers.queue.clear()
        current["confirm"] = job.get("confirm") is True
        with tempfile.TemporaryDirectory() as directory:
            try:
                paths = []
                for item in job.get("attachments", ()):
                    paths.append(os.path.join(directory, item["name"]))
                    with open(paths[-1], "wb") as out:
                        out.write(base64.b64decode(item["content"]))
                client.send(job["prompt"], attachments=paths,
                            ask=ask if job.get("interactive") is True else (lambda question: ""),
                            cancelled=cancelled.is_set)
                for kind, name, data in artifacts(client, directory):
                    put(op="blob", kind=kind, name=name, content=base64.b64encode(data).decode())
            except Exception as error:
                put(op="error", message="%s: %s" % (type(error).__name__, error))
            else:
                put(op="done")
        cancelled.clear()
    client.close()


if __name__ == "__main__":
    main()
