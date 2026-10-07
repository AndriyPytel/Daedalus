"""
The worker process, launched as `python -m service.worker`: the only service module that reaches the engine, and only through `sdk.Client`.

It reads `{"op": "send", "prompt": ...}` lines from stdin and writes `ready`, `event`, `done` and `error` lines to the real stdout, ending when stdin closes.
Anything else the engine prints goes to stderr, so it cannot corrupt the channel.
"""
import json
import sys

from sdk import Client


def main():
    channel, sys.stdout = sys.stdout, sys.stderr

    def put(**message):
        channel.write(json.dumps(message, default=str) + "\n")
        channel.flush()

    client = Client()
    client.watch(lambda event: put(op="event", kind=event.kind, at=event.at, data=event.data))
    put(op="ready")
    for line in sys.stdin:
        try:
            client.send(json.loads(line)["prompt"])
        except Exception as error:
            put(op="error", message="%s: %s" % (type(error).__name__, error))
        else:
            put(op="done")
    client.close()


if __name__ == "__main__":
    main()
