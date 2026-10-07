"""
The worker process, launched as `python -m service.worker`: the only service module that reaches the engine, and only through `sdk.Client`.

It reads `send`, `cancel` and `answer` lines from stdin while a Run is in progress and writes `ready`, `event`, `question`, `done` and `error` lines to the real stdout, ending when stdin closes.
A Run sent `interactive` asks through the channel and waits for its `answer`, any other decides alone, and an Owned Object `confirm` is refused unless the Run was sent `confirm`; anything else the engine prints goes to stderr.
"""
import json
import queue
import sys
import threading

from sdk import Client


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
        try:
            client.send(job["prompt"], ask=ask if job.get("interactive") is True else (lambda question: ""),
                        cancelled=cancelled.is_set)
        except Exception as error:
            put(op="error", message="%s: %s" % (type(error).__name__, error))
        else:
            put(op="done")
        cancelled.clear()
    client.close()


if __name__ == "__main__":
    main()
