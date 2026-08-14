"""The distinct Model Mania drawings and recorded numbers used as evidence."""
import json
from pathlib import Path


HERE = Path(__file__).parent
SHEETS = {
    "mm25-p1-1.pdf": HERE / "modelmania/mm25-p1-1.pdf",
    "mm25-p2.pdf": HERE / "modelmania/mm25-p2.pdf",
    "model-mania-2024.pdf": HERE / "modelmania/model-mania-2024.pdf",
    "model-mania_-in-person-2023 copy.pdf": (
        HERE / "modelmania/model-mania_-in-person-2023 copy.pdf"),
}
DESCRIBED = HERE / "recorded/described_numbers.json"


def read_sheets(tmp_path_factory):
    """Read each distinct drawing once, avoiding the duplicate 2023 PDF."""
    from core import sheet_reader

    return {
        name: sheet_reader.read(str(path), into=str(tmp_path_factory.mktemp(
            "modelmania-%d" % at)))
        for at, (name, path) in enumerate(SHEETS.items())
    }


def described_numbers():
    """The numbers stated by the recorded descriptions, grouped by their sheet."""
    with DESCRIBED.open(encoding="utf-8") as reading:
        return json.load(reading)


def verdicts(sheets):
    """Build every traced outline against the dimensions its sheet supplies."""
    from core import sketch_check

    return [
        (name, sketch_check.checked(outline, view.view, sheet.between))
        for name, sheet in sheets.items()
        for view in sheet.drawn
        for outline in view.outlines
    ]
