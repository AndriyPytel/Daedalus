"""
The worker entry for the end-to-end tests, run as `python deploy/worker/scripted_worker.py`: `service.worker` over a `ScriptedLLMClient`, because the image holds no provider credentials.
`DAE_SCRIPT` picks the turns: `cube` builds one 10 mm cube, `ask` asks "solid or hollow?" first, `delete` removes an object the assistant did not create, `edit` is what a worker opened on a checkpoint plays (it lengthens the box), `DAE_CALLS` names a file that logs every message list the LLM receives, and `DAE_DELAY` seconds is how long every turn takes.
"""
import json
import os
import time
import uuid

from sdk import Client, ScriptedLLMClient

from service import worker

WORKER = uuid.uuid4().hex
DESIGN = json.dumps({"declare": {"design": {}, "question": ""}})
ASKING = json.dumps({"declare": {"design": {}, "question": "solid or hollow?"}})
STEPS = json.dumps({"declare": {"steps": ["Add a 10mm cube"]}})
CODE = json.dumps({"code": "doc.addObject('Part::Box', 'Box')", "step": 1, "label": "Add the box"})
DELETING = json.dumps({"code": "doc.removeObject('UserBox')", "step": 1, "label": "Remove the box"})
LONG_DESIGN = json.dumps({"declare": {"design": {"features": {"Box": {"length": 20}}}, "question": ""}})
LONGER = json.dumps({"code": "doc.getObject('Box').Length = design['features']['Box']['length']", "step": 1, "label": "Make the box 20mm long"})
TAIL = [
    json.dumps({"declare": {"steps": [{"step": 1, "label": "Add the box"}]}}),
    json.dumps({"declare": {"suspected": []}}),
    json.dumps({"declare": {"verdict": "pass", "reason": "A 10mm cube."}}),
]
SCRIPTS = {
    "cube": ["design", DESIGN, STEPS, CODE] + TAIL,
    "ask": ["design", ASKING, DESIGN, STEPS, CODE] + TAIL,
    "delete": ["design", DESIGN, STEPS, DELETING] + TAIL,
    "edit": ["design", LONG_DESIGN, json.dumps({"declare": {"steps": ["Make the box 20mm long"]}}), LONGER] + TAIL,
}


class Slow(ScriptedLLMClient):
    def stream(self, messages, **overrides):
        if os.environ.get("DAE_CALLS"):
            with open(os.environ["DAE_CALLS"], "a") as log:
                log.write(json.dumps({"worker": WORKER, "messages": messages}) + "\n")
        time.sleep(float(os.environ.get("DAE_DELAY", "0")))
        return super().stream(messages, **overrides)


def build(**options):
    script = "edit" if options.get("document") else os.environ.get("DAE_SCRIPT", "cube")
    client = Client(llm=Slow(SCRIPTS[script]), **options)
    if script == "delete":
        client.document.addObject("Part::Box", "UserBox")
        client.document.recompute()
    return client


if __name__ == "__main__":
    worker.Client = build
    worker.main()
