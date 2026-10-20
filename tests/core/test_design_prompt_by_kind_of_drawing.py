"""The picture paragraph of the Design prompt is shown for a picture, and the text layer and line work paragraphs for a PDF."""
from core.loop.prompt import a_raster_picture, prompt
from core.nobody import nobody_answering


def test_design_is_told_about_a_picture_or_about_a_text_layer_by_the_drawing_it_is_given():
    picture = prompt("design", raster=True)
    pdf = prompt("design")

    assert "there is no text layer, no traced line work" in picture
    assert "Where the drawing is a PDF you are also given **the text layer**" not in picture
    assert "write the dimension table" in picture
    assert "Where the drawing is a PDF you are also given **the text layer**" in pdf
    assert "write the dimension table" not in pdf
    assert (a_raster_picture(["a.PNG"]), a_raster_picture(["a.pdf"]), a_raster_picture([])) == \
        (True, False, False)


def test_design_is_told_to_correct_a_value_only_where_nobody_answers():
    with nobody_answering():
        headless = " ".join(prompt("design").split())
    asked = " ".join(prompt("design").split())

    assert "A correction replaces the old value" in headless and "`corrects`" in headless
    assert "A field already filled cannot change" not in headless
    assert "A field already filled cannot change" in asked and "A correction replaces" not in asked
