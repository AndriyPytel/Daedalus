"""
Level 1 -- a Task opens a fixture folder, a sheet or a bare Intent into the Domain, Intent, attachments and seed a run starts from.
"""
import ast
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src", "tools"))

import sdk  # noqa: E402
import task  # noqa: E402
from task import Task  # noqa: E402


def _folder(tmp_path, files, task_json=None):
    folder = tmp_path / "fixture"
    folder.mkdir()
    for relative in files:
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    if task_json is not None:
        (folder / "task.json").write_text(json.dumps(task_json))
    return str(folder)


def _edit(tmp_path):
    folder = _folder(tmp_path, ["input.step", "edit_description.txt"])
    (tmp_path / "fixture" / "edit_description.txt").write_text("Cut a 10 mm bore.\n")
    return folder, {
        "domain": sdk.PART, "suffix": ".step",
        "attachments": ["renders/front.png", "renders/right.png", "renders/top.png", "renders/iso.png"],
        "seed": (task.STEP, "input.step")}


def _from_parts(tmp_path):
    return _folder(tmp_path, ["parts/b.step", "parts/a.step"], {
        "text": "Assemble.", "inputs": {"parts": [{"file": "parts/b.step"}, {"file": "parts/a.step"}]}}), {
        "domain": sdk.ASSEMBLY, "suffix": ".FCStd", "intent": "Assemble.",
        "attachments": ["parts/b.step", "parts/a.step"], "seed": None}


def _from_scratch(tmp_path):
    return _folder(tmp_path, [], {"text": "Model it.", "inputs": {"graph": {"nodes": []}}}), {
        "domain": sdk.ASSEMBLY, "suffix": ".FCStd", "attachments": [], "seed": None}


def _assembly_edit(tmp_path):
    return _folder(tmp_path, ["input.FCStd", "parts/p.step"], {
        "text": "Remove Part4.", "inputs": {"input": "input.FCStd", "edit": "remove"}}), {
        "domain": sdk.ASSEMBLY, "suffix": ".FCStd", "intent": "Remove Part4.",
        "attachments": ["parts/p.step"], "seed": (task.DOCUMENT, "input.FCStd")}


def _bim_create(tmp_path):
    return _folder(tmp_path, ["plan.png"], {
        "text": "Build the plan.", "inputs": {"plan": "plan.png"}}), {
        "domain": sdk.BIM, "suffix": ".ifc", "intent": "Build the plan.",
        "attachments": ["plan.png"], "seed": None}


def _bim_edit(tmp_path):
    return _folder(tmp_path, ["input.FCStd", "input.ifc"], {
        "text": "Add a door.", "inputs": {"input": "input.FCStd", "input_ifc": "input.ifc", "edit": "add_door"}}), {
        "domain": sdk.BIM, "suffix": ".ifc", "intent": "Add a door.",
        "attachments": [], "seed": (task.DOCUMENT, "input.FCStd")}


@pytest.mark.parametrize("layout", [_edit, _from_parts, _from_scratch, _assembly_edit, _bim_create, _bim_edit])
def test_a_task_folder_opens_into_its_domain_intent_and_attachments(tmp_path, layout):
    folder, expected = layout(tmp_path)

    opened = Task.open(folder, "an intent the folder does not use")

    assert opened.domain == expected["domain"]
    assert opened.saved_suffix == expected["suffix"]
    assert opened.attachments == [os.path.join(folder, name) for name in expected["attachments"]]
    assert opened.seed == (task.Seed(expected["seed"][0], os.path.join(folder, expected["seed"][1]))
                           if expected["seed"] else None)
    assert "an intent the folder does not use" not in opened.intent
    assert opened.intent.startswith(expected.get("intent", ""))


def test_an_edit_folder_s_intent_is_its_description_in_the_edit_prompt(tmp_path):
    folder, _ = _edit(tmp_path)

    assert "Cut a 10 mm bore." in Task.open(folder).intent


def test_an_assembly_from_scratch_puts_its_graph_in_the_intent(tmp_path):
    folder, _ = _from_scratch(tmp_path)

    assert Task.open(folder).intent.startswith("Model it.\n\nThe graph:\n")


def test_a_bim_edit_opens_its_ifc_natively_when_asked(tmp_path):
    folder, _ = _bim_edit(tmp_path)

    assert Task.open(folder, native=True).seed == task.Seed(task.IFC, os.path.join(folder, "input.ifc"))


def test_a_sheet_is_attached_to_the_intent_it_is_given():
    opened = Task.open("/data/sheet.pdf", "Build it.")

    assert (opened.domain, opened.intent, opened.attachments, opened.seed, opened.saved_suffix) == (
        sdk.PART, "Build it.", ["/data/sheet.pdf"], None, ".step")


def test_a_bare_intent_is_a_part_with_nothing_attached():
    opened = Task.open(intent="Model an M12 nut.")

    assert (opened.domain, opened.intent, opened.attachments, opened.seed) == (
        sdk.PART, "Model an M12 nut.", [], None)


def test_a_sheet_or_a_bare_task_needs_an_intent():
    with pytest.raises(ValueError):
        Task.open("/data/sheet.pdf")
    with pytest.raises(ValueError):
        Task.open()


def test_the_lane_imports_no_freecad():
    """No tool imports FreeCAD, Part or Import except `run.py`, which launches the kernel, and the `seed_*` functions of the Task module."""
    banned = {"FreeCAD", "FreeCADGui", "Part", "Import"}

    def imports(tree):
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        return found

    tools = os.path.join(ROOT, "src", "tools")
    for name in sorted(os.listdir(tools)):
        if not name.endswith(".py") or name == "run.py":
            continue
        path = os.path.join(tools, name)
        tree = ast.parse(open(path, encoding="utf-8").read(), path)
        tree.body = [node for node in tree.body
                     if not (isinstance(node, ast.FunctionDef) and node.name.startswith("seed_"))]
        assert not imports(tree) & banned, name
