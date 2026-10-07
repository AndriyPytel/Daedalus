"""
A stand-in for `service.worker` that speaks the channel with no FreeCAD, steered by the prompt it is sent.

`hello` emits two events and finishes, `priced` does the same with a `cost` event of 0.25 USD between them, `hang` starts a child process, reports its pid and never ends, `cut` writes half
an event line and exits, `bye` exits at once, `gated:<path>` emits five events, waits for the file `<path>` to exist, then emits five more and finishes. End of input ends the process unless the prompt was `hang`.
"""
import json
import os
import subprocess
import sys
import time


def put(**message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main():
    put(op="ready")
    for line in sys.stdin:
        prompt = json.loads(line)["prompt"]
        if prompt == "hello":
            put(op="event", kind="intent", at=1.0, data={"prompt": prompt})
            put(op="event", kind="completed", at=2.0, data={"summary": "done"})
            put(op="done")
        elif prompt == "priced":
            put(op="event", kind="cost", at=1.0, data={"usd": 0.25})
            put(op="event", kind="completed", at=2.0, data={"summary": "done"})
            put(op="done")
        elif prompt == "hang":
            child = subprocess.Popen(["sleep", "300"])
            put(op="event", kind="intent", at=1.0, data={"pid": os.getpid(), "child": child.pid})
            while True:
                time.sleep(1)
        elif prompt == "cut":
            put(op="event", kind="intent", at=1.0, data={})
            sys.stdout.write('{"op": "event", "kind": "thou')
            sys.stdout.flush()
            os._exit(0)
        elif prompt.startswith("gated:"):
            for step in range(5):
                put(op="event", kind="step", at=float(step), data={"n": step})
            while not os.path.exists(prompt[len("gated:"):]):
                time.sleep(0.02)
            for step in range(5, 10):
                put(op="event", kind="step", at=float(step), data={"n": step})
            put(op="event", kind="completed", at=10.0, data={"summary": "done"})
            put(op="done")
        elif prompt == "boom":
            put(op="error", message="provider refused")
        elif prompt == "bye":
            os._exit(3)


if __name__ == "__main__":
    main()
