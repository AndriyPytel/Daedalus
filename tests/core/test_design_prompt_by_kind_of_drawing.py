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


def test_design_is_told_an_overall_is_not_the_largest_number_of_a_chain():
    said = " ".join(prompt("design").split())

    assert ("the largest number in a chain measured from a datum is not the part's overall "
            "unless the chain ends at the far face") in said
    assert "take it from the view's own extent (the bottom outline), not from the chain" in said


def test_design_reads_a_section_along_a_rib_as_the_rib_and_keeps_a_shown_feature_out_of_not_defined():
    rib = ("find its cutting line in the view that carries it,** and write in that value's `from` "
           "what the line crosses there")
    feature = "`not_defined` holds a value, never a whole feature"
    with nobody_answering():
        headless = " ".join(prompt("design").split())
    asked = " ".join(prompt("design").split())

    assert rib in headless and rib in asked
    assert "where no printed number gives a floor between the ribs, the recess goes through" in asked
    assert feature in headless and feature not in asked


def test_design_reads_goes_through_and_never_assumes_it():
    said = " ".join(prompt("design").split())

    assert "`goes_through: true` is read, never assumed: a hole, slot or window goes through only where a view shows it" in said
    assert "Where nothing shows it, it is blind, with a depth measured to scale or assumed, flagged and with `because`" in said
