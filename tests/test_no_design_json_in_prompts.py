"""
The Design model's YAML part-file is its one serialized form; nothing
in this run ever writes a `design.json`, so no prompt may promise a file
that does not exist to open.
"""
import os

from tests.source_tree import ROOT

PROMPTS_DIR = os.path.join(ROOT, "config", "prompts")


def test_no_prompt_mentions_design_json():
    offenders = []
    for dirpath, _, names in os.walk(PROMPTS_DIR):
        for name in sorted(names):
            if not name.endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as handle:
                if "design.json" in handle.read():
                    offenders.append(os.path.relpath(path, ROOT))
    assert not offenders, (
        "design.json is never written to a phase's working directory; "
        "these prompts still promise it: %s" % offenders)
