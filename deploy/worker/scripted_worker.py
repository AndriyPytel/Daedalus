"""
The worker entry for the end-to-end test: `service.worker` over a `ScriptedLLMClient` that builds one 10 mm cube, because the image holds no provider credentials.
"""
import functools
import json

from core.llm import ScriptedLLMClient
from sdk import Client

from service import worker

SCRIPT = [
    "design",
    json.dumps({"declare": {"design": {}, "question": ""}}),
    json.dumps({"declare": {"steps": ["Add a 10mm cube"]}}),
    json.dumps({"code": "doc.addObject('Part::Box', 'Box')", "step": 1, "label": "Add the box"}),
    json.dumps({"declare": {"steps": [{"step": 1, "label": "Add the box"}]}}),
    json.dumps({"declare": {"suspected": []}}),
    json.dumps({"declare": {"verdict": "pass", "reason": "A 10mm cube."}}),
]


if __name__ == "__main__":
    worker.Client = functools.partial(Client, llm=ScriptedLLMClient(SCRIPT))
    worker.main()
