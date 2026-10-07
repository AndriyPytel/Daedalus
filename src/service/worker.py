"""
The worker process, launched as `python -m service.worker`: the only service module that reaches the engine, and only through `sdk.Client`.

It reads `send` lines (a prompt and base64 attachments) from stdin and writes `ready`, `event`, `blob`, `done` and `error` lines to the real stdout, ending when stdin closes.
After a send the Run's STEP, when the model has a solid, and its FCStd checkpoint go out as base64 `blob` lines; anything else the engine prints goes to stderr.
"""
import base64
import json
import os
import sys
import tempfile

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

    def put(**message):
        channel.write(json.dumps(message, default=str) + "\n")
        channel.flush()

    client = Client()
    client.watch(lambda event: put(op="event", kind=event.kind, at=event.at, data=event.data))
    put(op="ready")
    for line in sys.stdin:
        request = json.loads(line)
        with tempfile.TemporaryDirectory() as directory:
            try:
                paths = []
                for item in request.get("attachments", ()):
                    paths.append(os.path.join(directory, item["name"]))
                    with open(paths[-1], "wb") as out:
                        out.write(base64.b64decode(item["content"]))
                client.send(request["prompt"], attachments=paths)
                for kind, name, data in artifacts(client, directory):
                    put(op="blob", kind=kind, name=name, content=base64.b64encode(data).decode())
            except Exception as error:
                put(op="error", message="%s: %s" % (type(error).__name__, error))
            else:
                put(op="done")
    client.close()


if __name__ == "__main__":
    main()
