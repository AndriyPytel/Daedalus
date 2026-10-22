"""
Build complexity of the generated sheets against the 32 public CADGenBench editing parts: per part
`features` (distinct dimension groups) and `directions` (distinct hole-axis and face-normal
directions), summarised as min/p25/median/p90/max. Run: `harness/.venv/bin/python harness/complexity.py OUT.md`.
"""
import json
import sys
from pathlib import Path

import numpy as np

HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS))

import abc_pool  # noqa: E402
import sheet_dimensions  # noqa: E402

REPO = "HuggingAI4Engineering/cadgenbench-data"
CACHE = Path.home() / ".cache" / "cadgenbench"
QUANTILES = (0, 25, 50, 90, 100)


def cadgenbench_complexity():
    from huggingface_hub import hf_hub_download, list_repo_files

    names = [f for f in list_repo_files(REPO, repo_type="dataset") if f.endswith("input.step")]
    rows = []
    for name in sorted(names):
        path = hf_hub_download(REPO, name, repo_type="dataset", cache_dir=CACHE)
        rows.append(sheet_dimensions.complexity(abc_pool.read_shape(path)))
    return rows


def sheet_complexity(test_data):
    return [
        {k: json.loads(p.read_text())[k] for k in ("features", "directions")}
        for p in sorted(Path(test_data).glob("*/*/source.json"))
    ]


def row(label, rows, key):
    values = [r[key] for r in rows]
    return f"| {label} | {key} | {len(values)} | " + " | ".join(f"{np.percentile(values, q):g}" for q in QUANTILES) + " |"


def table(sheets, bench):
    lines = ["| set | metric | parts | min | p25 | median | p90 | max |", "|---|---|---|---|---|---|---|---|"]
    for key in ("features", "directions"):
        lines += [row("ABC sheets", sheets, key), row("CADGenBench inputs", bench, key)]
    return "\n".join(lines)


if __name__ == "__main__":
    text = table(sheet_complexity(HARNESS.parent / "test-data"), cadgenbench_complexity())
    Path(sys.argv[1]).write_text(text + "\n")
    print(text)
