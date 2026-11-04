import os
import subprocess

import pytest

import assembly_score

FREECADCMD = assembly_score.FREECADCMD
needs_freecad = pytest.mark.skipif(not os.path.exists(FREECADCMD), reason="freecadcmd missing")

IDENTITY = [0, 0, 0, 0, 0, 0, 1]


def at(x, y=0, z=0):
    return [x, y, z, 0, 0, 0, 1]


def box_part(label, size, x):
    return {"label": label, "box": size, "placement": at(x)}


def three_boxes(c_frame=at(0), c_type="Revolute", c_x=60, conflict=False):
    spec = {
        "parts": [box_part("A", [40, 20, 10], 0), box_part("B", [20, 20, 10], 40), box_part("C", [10, 10, 10], c_x)],
        "joints": [
            {"type": "Fixed", "a": "A", "b": "B", "frame_a": at(40), "frame_b": IDENTITY},
            {"type": c_type, "a": "B", "b": "C", "frame_a": at(20), "frame_b": c_frame},
        ],
        "ground": "A",
    }
    if conflict:
        spec["joints"] += [
            {"type": "Revolute", "a": "A", "b": "B", "frame_a": at(40), "frame_b": IDENTITY},
            {"type": "Revolute", "a": "A", "b": "B", "frame_a": [40, 0, 0, 0.7071, 0, 0, 0.7071], "frame_b": IDENTITY},
            {"type": "Fixed", "a": "A", "b": "B", "frame_a": at(41), "frame_b": IDENTITY},
        ]
    return spec


def measure_of(parts, joints, solve=0, interference=0.0, rot=None):
    return {
        "solve": solve,
        "parts": {label: {"bbox": box, "com": [(box[0] + box[3]) / 2, (box[1] + box[4]) / 2, (box[2] + box[5]) / 2], "volume": 1.0,
                        "rot": (rot or {}).get(label, [0, 0, 0, 1])}
                  for label, box in parts.items()},
        "joints": joints,
        "interference": interference,
        "diagonal": 100.0,
    }


BOXES = {"A": [0, 0, 0, 10, 10, 10], "B": [10, 0, 0, 20, 10, 10]}
JOINT = {"type": "Fixed", "a": "A", "b": "B"}


def test_reference_against_itself_is_one():
    reference = measure_of(BOXES, [JOINT])
    assert assembly_score.combine(reference, reference)["score"] == 1.0


def test_unsolved_candidate_scores_zero():
    reference = measure_of(BOXES, [JOINT])
    assert assembly_score.combine(measure_of(BOXES, [JOINT], solve=-6), reference)["score"] == 0.0


def test_missing_reference_part_scores_zero():
    reference = measure_of(BOXES, [JOINT])
    candidate = measure_of({"A": BOXES["A"]}, [])
    assert assembly_score.combine(candidate, reference)["score"] == 0.0


def test_moved_part_lowers_only_the_placement_component():
    reference = measure_of(BOXES, [JOINT])
    moved = measure_of({**BOXES, "B": [30, 0, 0, 40, 10, 10]}, [JOINT])
    result = assembly_score.combine(moved, reference)
    assert 0 < result["placement"] < 1
    assert (result["joints"], result["interference"]) == (1.0, 1.0)
    assert result["score"] < 1


def test_wrong_joint_type_halves_the_joint_component():
    reference = measure_of(BOXES, [JOINT, {"type": "Revolute", "a": "A", "b": "B"}])
    wrong = measure_of(BOXES, [JOINT, {"type": "Slider", "b": "A", "a": "B"}])
    assert assembly_score.combine(wrong, reference)["joints"] == 0.5


def test_interference_volume_lowers_its_component():
    reference = measure_of(BOXES, [JOINT])
    overlap = measure_of(BOXES, [JOINT], interference=0.5)
    assert assembly_score.combine(overlap, reference)["interference"] == 0.75
    assert assembly_score.combine(measure_of(BOXES, [JOINT], interference=5.0), reference)["interference"] == 0.0


def test_interference_below_boolean_noise_counts_as_none():
    reference = measure_of(BOXES, [JOINT])
    assert assembly_score.combine(measure_of(BOXES, [JOINT], interference=1e-9), reference)["score"] == 1.0


def test_assembly_without_joints_matches_itself():
    alone = measure_of({"A": BOXES["A"]}, [])
    assert assembly_score.combine(alone, alone)["score"] == 1.0


def test_extra_candidate_part_counts_against_placement():
    reference = measure_of(BOXES, [JOINT])
    extra = measure_of({**BOXES, "X": [0, 0, 0, 1, 1, 1]}, [JOINT])
    assert assembly_score.combine(extra, reference)["placement"] < 1


def test_half_turn_of_a_part_lowers_the_placement_component():
    reference = measure_of(BOXES, [JOINT])
    turned = measure_of(BOXES, [JOINT], rot={"B": [0, 0, 1, 0]})
    result = assembly_score.combine(turned, reference)
    assert result["placement"] == 0.75
    assert result["score"] < 1


def test_quarter_turn_is_penalised_by_half_the_half_turn():
    reference = measure_of(BOXES, [JOINT])
    quarter = measure_of(BOXES, [JOINT], rot={"B": [0, 0, 0.7071068, 0.7071068]})
    assert assembly_score.combine(quarter, reference)["placement"] == pytest.approx(0.875, abs=1e-6)


def test_candidate_without_joints_scores_zero_when_the_reference_has_joints():
    reference = measure_of(BOXES, [JOINT])
    assert assembly_score.combine(measure_of(BOXES, []), reference)["score"] == 0.0


def test_score_is_joint_match_times_the_mean_of_placement_and_interference():
    reference = measure_of(BOXES, [JOINT, {"type": "Revolute", "a": "A", "b": "B"}])
    candidate = measure_of({**BOXES, "B": [30, 0, 0, 40, 10, 10]}, [JOINT], interference=0.5)
    result = assembly_score.combine(candidate, reference)
    assert result["score"] == pytest.approx(result["joints"] * (result["placement"] + result["interference"]) / 2)
    assert result["joints"] == 0.5


def turned_c():
    spec = three_boxes(c_type="Fixed", c_frame=[10, 10, 0, 0, 0, 1, 0])
    return spec


@pytest.fixture(scope="module")
def docs(tmp_path_factory):
    out = tmp_path_factory.mktemp("assembly")
    variants = {
        "reference": three_boxes(),
        "moved": three_boxes(c_frame=at(0, -5)),
        "wrong_type": three_boxes(c_type="Fixed"),
        "interfering": three_boxes(c_frame=at(10)),
        "unsolvable": three_boxes(conflict=True),
        "half_turn": turned_c(),
    }
    paths = {name: out / f"{name}.FCStd" for name in variants}
    assembly_score.build([(variants[name], paths[name]) for name in variants])
    return paths


@needs_freecad
def test_reference_document_solves_and_scores_one_against_itself(docs):
    result = assembly_score.score(docs["reference"], docs["reference"])
    assert result["solve"] == 0
    assert result["score"] == 1.0


@needs_freecad
@pytest.mark.parametrize("name,component", [
    ("moved", "placement"), ("wrong_type", "joints"), ("interfering", "interference"),
])
def test_perturbed_document_scores_below_reference(docs, name, component):
    result = assembly_score.score(docs[name], docs["reference"])
    assert result["score"] < 1
    assert result[component] < 1


@needs_freecad
def test_symmetric_part_turned_half_a_turn_about_its_centre_scores_below_one(docs):
    result = assembly_score.score(docs["half_turn"], docs["wrong_type"])
    assert result["placement"] < 1
    assert result["score"] < 1


@needs_freecad
def test_unsolvable_document_scores_zero(docs):
    result = assembly_score.score(docs["unsolvable"], docs["reference"])
    assert result["solve"] != 0
    assert result["score"] == 0.0


@needs_freecad
def test_repeat_gives_the_same_score(docs):
    first = assembly_score.score(docs["moved"], docs["reference"])
    assert first == assembly_score.score(docs["moved"], docs["reference"])


@needs_freecad
def test_a_part_whose_shape_is_a_compound_is_measured(tmp_path):
    path = tmp_path / "compound.FCStd"
    script = tmp_path / "make.py"
    script.write_text(f"""
import FreeCAD as App, JointObject, Part
doc = App.newDocument("A")
assembly = doc.addObject("Assembly::AssemblyObject", "Assembly")
assembly.Type = "Assembly"
group = assembly.newObject("Assembly::JointGroup", "Joints")
part = doc.addObject("Part::Feature", "A")
part.Shape = Part.Compound([Part.makeBox(10, 20, 30)])
assembly.addObject(part)
doc.recompute()
JointObject.GroundedJoint(group.newObject("App::FeaturePython", "Ground"), part)
doc.saveAs({str(path)!r})
""")
    subprocess.run([FREECADCMD, "-c", f"exec(open({str(script)!r}).read())"], check=True, capture_output=True)

    (measured,) = assembly_score.measure([path])

    assert measured["parts"]["A"]["com"] == pytest.approx([5, 10, 15])
