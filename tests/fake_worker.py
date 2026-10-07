"""
A stand-in for `service.worker` that speaks the channel with no FreeCAD, steered by the prompt it is sent.

`make` emits an event, a STEP and an FCStd blob and finishes, `attachments` reports the name and sha256 of each attachment it received, `hello` emits two events and finishes, `priced` does the same with a `cost` event of 0.25 USD between them, `hang` starts a child process, reports its pid and never ends, `cut` writes half
an event line and exits, `bye` exits at once. End of input ends the process unless the prompt was `hang`.
"""
import base64
import hashlib
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
        request = json.loads(line)
        prompt = request["prompt"]
        if prompt == "make":
            put(op="event", kind="intent", at=1.0, data={"prompt": prompt})
            put(op="blob", kind="step", name="model.step", content=base64.b64encode(
                b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n").decode())
            put(op="blob", kind="checkpoint", name="model.FCStd", content=base64.b64encode(
                b"PK\x03\x04" + bytes(range(256)) * 20).decode())
            put(op="event", kind="completed", at=2.0, data={"summary": "done"})
            put(op="done")
        elif prompt == "attachments":
            got = [{"name": item["name"], "sha256": hashlib.sha256(base64.b64decode(item["content"])).hexdigest()}
                   for item in request["attachments"]]
            put(op="event", kind="received", at=1.0, data={"files": got})
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
        elif prompt == "boom":
            put(op="error", message="provider refused")
        elif prompt == "bye":
            os._exit(3)


if __name__ == "__main__":
    main()
