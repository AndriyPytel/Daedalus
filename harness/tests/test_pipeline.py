import json
from pathlib import Path

import pytest

import make_sheets
from conftest import box, drill, plate


def write_step(shape, path):
    make_sheets._write_step(shape, path)
    return path


needs_freecad = pytest.mark.skipif(not Path(make_sheets.FREECADCMD).exists(), reason="freecadcmd is not installed")


@needs_freecad
def test_a_part_gets_five_files_in_mm_with_the_iso_line_weights(plate, tmp_path):
    step = write_step(plate, tmp_path / "plate.step")
    out = tmp_path / "plate"
    report = make_sheets.build_sheet(step, out, "abc_0000")
    assert sorted(p.name for p in out.iterdir()) == ["dimensions.json", "exemplar.step", "input.png", "sheet.pdf", "source.json"]
    source = json.loads((out / "source.json").read_text())
    assert source["unit"] == "mm"
    assert source["line_weights_mm"] == [0.25, 0.35, 0.5]
    assert {d["source"] for d in json.loads((out / "dimensions.json").read_text())} == {"step"}
    import sheet_dimensions

    low, high = sheet_dimensions.bounds(make_sheets.abc_pool.read_shape(out / "exemplar.step"))
    assert list(high - low) == pytest.approx([100, 60, 20], rel=1e-3)
    assert report["overlaps"] == []


@needs_freecad
def test_the_section_view_exists_only_when_hidden_edges_pass_the_threshold(tmp_path):
    hollow = drill(box(100, 60, 40), 50, 30, 12, z=5, height=20)
    for name, shape, expected in (("hollow", hollow, True), ("solid", box(100, 60, 40), False)):
        step = write_step(shape, tmp_path / f"{name}.step")
        make_sheets.build_sheet(step, tmp_path / name)
        assert json.loads((tmp_path / name / "source.json").read_text())["section_view"] is expected


def test_a_part_with_no_readable_unit_is_skipped(tmp_path):
    step = tmp_path / "x.step"
    step.write_text("ISO-10303-21; DATA; ENDSEC;")
    with pytest.raises(make_sheets.Skip, match="unit"):
        make_sheets.build_sheet(step, tmp_path / "out")


def test_skip_reason_names_the_rule():
    assert make_sheets.skip_reason(["plane level z=5", "hole 3 not located on axis 0"]) == "coverage: plane level"
