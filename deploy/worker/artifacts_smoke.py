"""
Builds one 10 mm cube with a scripted LLM, takes `service.worker.artifacts`, reopens the checkpoint in FreeCAD and prints one JSON line.

Run as `python deploy/worker/artifacts_smoke.py` in the worker image.
"""
import json
import os
import tempfile


def main():
    from sdk import Client, ScriptedLLMClient
    from service.worker import artifacts

    llm = ScriptedLLMClient([
        "design",
        json.dumps({"declare": {"design": {}, "question": ""}}),
        json.dumps({"declare": {"steps": ["Add a 10mm cube"]}}),
        json.dumps({"code": "doc.addObject('Part::Box', 'Box')", "step": 1, "label": "Add the box"}),
        json.dumps({"declare": {"steps": [{"step": 1, "label": "Add the box"}]}}),
        json.dumps({"declare": {"suspected": []}}),
        json.dumps({"declare": {"verdict": "pass", "reason": "A 10mm cube."}}),
    ])
    client = Client(llm=llm)
    with tempfile.TemporaryDirectory() as directory:
        try:
            client.send("Add a 10mm cube")
            found = artifacts(client, directory)
        finally:
            client.close()
        by_kind = {kind: (name, data) for kind, name, data in found}
        path = os.path.join(directory, "reopened.FCStd")
        with open(path, "wb") as out:
            out.write(by_kind["checkpoint"][1])
        reopened = Client(document=path)
        try:
            solids = sum(len(obj.Shape.Solids) for obj in reopened.document.Objects if hasattr(obj, "Shape"))
        finally:
            reopened.close()
    print(json.dumps({"kinds": [kind for kind, _, _ in found], "step_name": by_kind["step"][0],
                      "checkpoint_name": by_kind["checkpoint"][0], "reopened_solids": solids,
                      "step_header": by_kind["step"][1].decode().splitlines()[0]}))


if __name__ == "__main__":
    main()
