"""
A stand-in for `service.worker` that speaks the channel with no FreeCAD, steered by the prompt it is sent.

`hello` emits two events and finishes, `priced` does the same with a `cost` event of 0.25 USD between them, `hang` starts a child process, reports its pid and never ends, `cut` writes half
an event line and exits, `bye` exits at once, `gated:<path>` emits five events, waits for the file `<path>` to exist, then emits five more and finishes. End of input ends the process unless the prompt was `hang`.
`flags` reports the `interactive` and `confirm` it was sent, `slow` steps until a `cancel` line arrives then reports `stopped`, `asks` puts a question when sent `interactive` and waits for the `answer` line, and `nags` puts a question whatever it was sent.
"""
import json
import os
import subprocess
import sys
import time


def put(**message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def await_op(name):
    for line in sys.stdin:
        message = json.loads(line)
        if message["op"] == name:
            return message


def main():
    put(op="ready")
    for line in sys.stdin:
        sent = json.loads(line)
        prompt = sent["prompt"]
        if prompt == "flags":
            put(op="event", kind="flags", at=1.0, data={"interactive": sent.get("interactive"), "confirm": sent.get("confirm")})
            put(op="done")
        elif prompt == "slow":
            put(op="event", kind="step", at=1.0, data={"n": 1})
            await_op("cancel")
            put(op="event", kind="stopped", at=2.0, data={"reason": "cancelled"})
            put(op="done")
        elif prompt == "asks":
            put(op="event", kind="question", at=1.0, data={"text": "solid or hollow?"})
            if sent.get("interactive"):
                put(op="question", text="solid or hollow?")
                answer = await_op("answer")["text"]
                put(op="event", kind="answer", at=2.0, data={"text": answer, "by": "user"})
            else:
                put(op="event", kind="answer", at=2.0, data={"text": "", "by": "nobody"})
            put(op="event", kind="completed", at=3.0, data={})
            put(op="done")
        elif prompt == "nags":
            put(op="question", text="anything?")
            put(op="event", kind="completed", at=3.0, data={})
            put(op="done")
        elif prompt == "hello":
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
