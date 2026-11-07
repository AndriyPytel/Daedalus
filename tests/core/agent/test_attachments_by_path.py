"""
Level 1 -- a file the provider does not take reaches every Domain's first message as its path, and a picture still as a picture.
"""
import pytest

from core.agent import assembly, bim, reader


@pytest.fixture
def files(tmp_path):
    picture, model = tmp_path / "plan.png", tmp_path / "input.ifc"
    picture.write_bytes(b"png")
    model.write_text("ISO-10303-21;")
    return str(picture), str(model)


@pytest.mark.parametrize("opening", [
    lambda paths: bim.opening("Build it.", paths),
    lambda paths: reader.opening("Build it.", None, None, paths, False),
    lambda paths: assembly.opening("Build it.", paths),
])
def test_a_file_the_provider_does_not_take_is_named_by_its_path(opening, files):
    picture, model = files

    blocks = opening(list(files))

    assert [b["type"] for b in blocks].count("image_url") == 1
    assert any(model in b.get("text", "") for b in blocks)
    assert not any(picture in b.get("text", "") for b in blocks)


def test_a_sheet_read_off_a_picture_still_names_the_other_files(files, monkeypatch):
    picture, model = files
    monkeypatch.setattr(reader, "_blocks", lambda sheet: [{"type": "image_url"}])

    blocks = reader.opening("Build it.", object(), None, list(files), False)

    assert any(model in b.get("text", "") for b in blocks)
