import json

import sheet_dimensions as sd


def _source(local_design):
    return json.loads((local_design / f"{local_design.name}.json").read_text())


def test_timeline_dimensions_carry_mm_values_on_the_face_on_view(local_design):
    dims = sd.timeline_dimensions(_source(local_design))
    sketch1 = [d for d in dims if d.feature == "Sketch1"]
    assert [(d.kind, round(d.value_mm, 6), d.view) for d in sketch1] == [("diameter", 32.0, "Front")]
    sketch5 = sorted(round(d.value_mm, 6) for d in dims if d.feature == "Sketch5")
    assert sketch5 == [12.0, 12.0]
    assert {d.view for d in dims if d.feature == "Sketch5"} == {"Front"}


def test_every_extrude_gets_a_depth_dimension(local_design):
    dims = sd.timeline_dimensions(_source(local_design))
    depths = {d.feature: round(d.value_mm, 6) for d in dims if d.kind == "depth"}
    assert depths["Extrude1"] == 10.0
    assert depths["Extrude4"] == 178.0
    assert len(depths) == 6


def _envelope():
    ends = [(10.0, 0.0, 0.0), (0.0, 0.0, 10.0), (0.0, 10.0, 0.0)]
    return [
        sd.Dim("envelope", "step", "envelope", 10.0, view, (0.0, 0.0, 0.0), end)
        for view, end in zip(("Front", "Front", "Top"), ends)
    ]


def test_missing_features_is_empty_for_a_full_set(local_design):
    source = _source(local_design)
    dims = sd.timeline_dimensions(source) + _envelope()
    assert sd.missing_features(source, dims) == []


def test_missing_features_names_the_dropped_feature(local_design):
    source = _source(local_design)
    dims = [d for d in sd.timeline_dimensions(source) if d.feature != "Extrude3"] + _envelope()
    assert sd.missing_features(source, dims) == ["Extrude3"]
