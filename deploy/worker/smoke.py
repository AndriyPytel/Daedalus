"""
Builds one 10 mm cube through `sdk.Client` on the real headless kernel, exports it as STEP, and prints one JSON line.

Run as `python deploy/worker/smoke.py /tmp/smoke.step`; the line also records what every `FreeCADGui` reader in `core.freecad_apply` answers with no display.
"""
import json
import sys


def main(out):
    from sdk import Client, ScriptedLLMClient, freecad_apply

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
    try:
        result = client.send("Add a 10mm cube")
        client.export_step(out)
        solids = len(client.document.Objects[0].Shape.Solids)
        gui = {
            "selection": freecad_apply._selection(),
            "active_view": freecad_apply._active_view(client.document),
            "review_views": freecad_apply.capture_review_views(["/tmp/view.png"], client.document),
        }
    finally:
        client.close()
    print(json.dumps({"ok": bool(result.ok), "reason": result.reason, "solids": solids, "gui": gui}))


if __name__ == "__main__":
    main(sys.argv[1])
