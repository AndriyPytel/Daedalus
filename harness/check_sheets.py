"""
Checks test-data/ after a run: sheets per split, the five files in each, dimension coverage re-run
against every exemplar.step and dimensions.json, skip reasons, overlap count, the line weights
found in the sheets and git-ignore status. Run: `harness/.venv/bin/python harness/check_sheets.py`.
"""
import json
import subprocess
import sys
from pathlib import Path

HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS))

import abc_pool  # noqa: E402
import sheet_dimensions  # noqa: E402

FILES = {"input.png", "sheet.pdf", "exemplar.step", "source.json", "dimensions.json"}


def check(data):
    data = Path(data)
    split = json.loads((data / "split.json").read_text())
    weights, sections, incomplete, short = set(), 0, [], []
    for group in ("test", "validation"):
        print(group, len(list((data / group).iterdir())))
        for sheet in sorted((data / group).iterdir()):
            if {p.name for p in sheet.iterdir()} != FILES:
                short.append(sheet.name)
            dims = [sheet_dimensions.Dim(**d) for d in json.loads((sheet / "dimensions.json").read_text())]
            if sheet_dimensions.missing(abc_pool.read_shape(sheet / "exemplar.step"), dims):
                incomplete.append(sheet.name)
            source = json.loads((sheet / "source.json").read_text())
            weights |= set(source["line_weights_mm"])
            sections += source["section_view"]
    print("sheets without all 5 files:", short)
    print("sheets with missing() not empty:", incomplete)
    print("skipped:", len(split["skipped"]), split["skip_counts"])
    print("sheets with overlaps:", len(split["overlaps"]), "overlap texts:", sum(map(len, split["overlaps"].values())))
    print("sheets with a section view:", sections)
    print("line weights mm:", sorted(weights))
    ignored = subprocess.run(["git", "check-ignore", "test-data"], cwd=HARNESS.parent, capture_output=True, text=True)
    print("git check-ignore test-data:", ignored.stdout.strip() or "NOT IGNORED")


if __name__ == "__main__":
    check(HARNESS.parent / "test-data")
