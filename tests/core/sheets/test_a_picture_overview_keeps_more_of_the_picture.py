"""A picture's overview is cut to 2576 px on its long edge, not 1400."""
from PIL import Image, ImageDraw

from core.sheets import sheet_reader


def test_a_large_picture_keeps_2576_px_on_its_long_edge_in_the_overview(tmp_path):
    path = tmp_path / "drawing.png"
    picture = Image.new("RGB", (4000, 3000), "white")
    ImageDraw.Draw(picture).rectangle((500, 500, 3500, 2500), outline="black", width=6)
    picture.save(path)

    sheet = sheet_reader.read(str(path), into=str(tmp_path / "cut"))

    with Image.open(sheet.overview) as overview:
        assert max(overview.size) == 2576
