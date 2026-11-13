"""
The worker entry for the end-to-end tests, run as `python deploy/worker/scripted_worker.py`: `service.worker` over a `ScriptedLLMClient`, because the image holds no provider credentials.
`DAE_SCRIPT` picks the turns of the loop: `cube` builds one 10 mm cube, `delete` removes an object the assistant did not create, which asks the user when the Run is `interactive`, `edit` is what a worker opened on a checkpoint plays (it lengthens the box), `DAE_CALLS` names a file that logs every message list the LLM receives, and `DAE_DELAY` seconds is how long every turn takes.
"""
import json
import os
import time
import uuid

from sdk import Client, ScriptedLLMClient

from service import worker

WORKER = uuid.uuid4().hex
CODE = json.dumps({"code": """
import Part
body = doc.addObject('PartDesign::Body', 'Body')
sketch = body.newObject('Sketcher::SketchObject', 'Sketch')
sketch.AttachmentSupport = [(body.Origin.OriginFeatures[3], '')]
sketch.MapMode = 'FlatFace'
for a, b in [((0, 0), (10, 0)), ((10, 0), (10, 10)), ((10, 10), (0, 10)), ((0, 10), (0, 0))]:
    sketch.addGeometry(Part.LineSegment(App.Vector(*a, 0), App.Vector(*b, 0)))
pad = body.newObject('PartDesign::Pad', 'Pad')
pad.Profile = sketch
pad.Length = 10
doc.recompute()
"""})
DELETING = json.dumps({"code": "doc.removeObject('UserBox')"})
LONGER = json.dumps({"code": "doc.getObject('Pad').Length = 20\ndoc.recompute()"})
FINISH = json.dumps({"finish": "done"})
SCRIPTS = {
    "cube": ["design", "part", CODE, FINISH],
    "delete": ["design", "part", DELETING, CODE, FINISH],
    "edit": ["design", "part", LONGER, FINISH],
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
        client.document.addObject("App::DocumentObject", "UserBox")
        client.document.recompute()
    return client


if __name__ == "__main__":
    worker.Client = build
    worker.main()
