"""The distinct Model Mania drawings and recorded numbers used as evidence."""
import json
import os
from pathlib import Path


HERE = Path(__file__).parent
MANUAL_DIR = HERE.parent / "manual"
MODELMANIA_DIR = Path(os.environ.get("FCC_MODELMANIA_DIR", MANUAL_DIR / "modelmania"))
RECORDED_DIR = MANUAL_DIR / "recorded"
BASELINE_SHEETS_FILE = MANUAL_DIR / "modelmania" / "baseline_sheets.json"
EXEMPLAR_DIR = MODELMANIA_DIR / "exemplar"
EXEMPLAR_FACTS_FILE = MANUAL_DIR / "modelmania" / "exemplar_facts.json"
SHEETS = {
    "mm25-p1-1.pdf": MODELMANIA_DIR / "mm25-p1-1.pdf",
    "mm25-p2.pdf": MODELMANIA_DIR / "mm25-p2.pdf",
    "model-mania-2024.pdf": MODELMANIA_DIR / "model-mania-2024.pdf",
    "model-mania_-in-person-2023 copy.pdf": (
        MODELMANIA_DIR / "model-mania_-in-person-2023 copy.pdf"),
}
PHASES_DIR = MODELMANIA_DIR / "phases"
PHASES = {
    "mm2023-phase1.pdf": PHASES_DIR / "mm2023-phase1.pdf",
    "mm2023-phase2.pdf": PHASES_DIR / "mm2023-phase2.pdf",
    "mm2024-phase1.pdf": PHASES_DIR / "mm2024-phase1.pdf",
    "mm2024-phase2.pdf": PHASES_DIR / "mm2024-phase2.pdf",
    "mm2025-phase1.pdf": PHASES_DIR / "mm2025-phase1.pdf",
    "mm2025-phase2.pdf": PHASES_DIR / "mm2025-phase2.pdf",
}
DESCRIBED = RECORDED_DIR / "described_numbers.json"
READING_2023 = RECORDED_DIR / "reading_2023_clamp.json"


def read_sheets(read_sheet):
    """Read each distinct drawing once, through the caller's sheet cache."""
    return {name: read_sheet(str(path)) for name, path in SHEETS.items()}


def exemplar_facts():
    """
    The one-time human check on each Exemplar: volume, bbox, face count,
    watertightness and Betti numbers, from the tracked
    `manual/modelmania/exemplar_facts.json` -- a repo fact, like
    `baseline_sheets.json`, even though the Exemplar STEPs it describes are
    third-party GrabCAD bytes and stay off git under `EXEMPLAR_DIR`.
    """
    with EXEMPLAR_FACTS_FILE.open(encoding="utf-8") as reading:
        return json.load(reading)


def exemplar_step(stem):
    """The Exemplar STEP path for *stem* (e.g. `"mm2025-phase1"`), present only when `EXEMPLAR_DIR` is."""
    return EXEMPLAR_DIR / (stem + ".step")


def baseline_sheets():
    """
    The 8 sheets a baseline run and its later comparison are both pinned to.
    Which 8 files is a repo fact read from the tracked
    `manual/modelmania/baseline_sheets.json`; where their bytes live today is
    an environment fact, so each name still resolves through `MODELMANIA_DIR`.
    """
    with BASELINE_SHEETS_FILE.open(encoding="utf-8") as reading:
        names = json.load(reading)["sheets"]
    return [MODELMANIA_DIR / name for name in names]


def described_numbers():
    """The numbers stated by the recorded descriptions, grouped by their sheet."""
    with DESCRIBED.open(encoding="utf-8") as reading:
        return json.load(reading)


def the_2023_clamp_as_read():
    """
    The whole of what Design gathered on the recorded run of the 2023 pipe clamp
    (`logs/sessions/fe663c12890b4d4497ad458136dfa880.jsonl`), which is not in
    the repo.
    """
    with READING_2023.open(encoding="utf-8") as reading:
        return json.load(reading)


def verdicts(sheets):
    """Build every traced outline against the dimensions its sheet supplies."""
    from core.sheets import sketch_check

    return [
        (name, sketch_check.checked(outline, view.view, sheet.between))
        for name, sheet in sheets.items()
        for view in sheet.drawn
        for outline in view.outlines
    ]
