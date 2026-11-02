import math

import assembly_tasks

IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def matrix(x=0.0, y=0.0, z=0.0, rotation=((1, 0, 0), (0, 1, 0), (0, 0, 1))):
    r = rotation
    return [r[0][0], r[0][1], r[0][2], x, r[1][0], r[1][1], r[1][2], y, r[2][0], r[2][1], r[2][2], z, 0, 0, 0, 1]


def automate(mate_type="FASTENED", fixed=0, second=None):
    mates = [{"mateType": mate_type, "occurrences": [0, 1], "mcfs": [matrix(0.01), matrix()]}]
    if second:
        mates.append({"mateType": second, "occurrences": [1, 2], "mcfs": [matrix(), matrix()]})
    return {
        "assemblyId": "abc",
        "parts": [{"id": "p1"}, {"id": "p2"}],
        "occurrences": [
            {"part": 0, "transform": IDENTITY, "fixed": fixed == 0},
            {"part": 1, "transform": matrix(0.1, 0, 0, ((0, -1, 0), (1, 0, 0), (0, 0, 1))), "fixed": fixed == 1},
            {"part": 1, "transform": matrix(0.2), "fixed": False},
        ],
        "mates": mates,
        "mateRelations": [], "mateGroups": [], "subAssemblies": [],
    }


def spec():
    return assembly_tasks.spec_from_automate(automate(), lambda part_id: f"/steps/{part_id}.step")


def test_spec_scales_metres_to_millimetres_and_names_parts_by_occurrence():
    result = spec()
    assert [p["label"] for p in result["parts"]] == ["Part1", "Part2", "Part3"]
    assert result["parts"][1]["placement"][:3] == [100.0, 0.0, 0.0]
    assert result["parts"][2]["step"] == "/steps/p2.step"
    assert result["joints"][0] == {"type": "Fixed", "a": "Part1", "b": "Part2", "frame_a": [10.0, 0, 0, 0, 0, 0, 1], "frame_b": [0, 0, 0, 0, 0, 0, 1]}


def test_spec_turns_a_rotation_matrix_into_a_unit_quaternion():
    qz, qw = spec()["parts"][1]["placement"][5:]
    assert math.isclose(qz, math.sqrt(0.5)) and math.isclose(qw, math.sqrt(0.5))


def test_ground_is_the_fixed_occurrence():
    assert spec()["ground"] == "Part1"
    result = assembly_tasks.spec_from_automate(automate(fixed=1), lambda part_id: part_id)
    assert result["ground"] == "Part2"


def test_unmappable_mate_type_rejects_the_assembly():
    assert assembly_tasks.spec_from_automate(automate(mate_type="PLANAR"), lambda part_id: part_id) is None
    assert assembly_tasks.spec_from_automate(automate(second="PIN_SLOT"), lambda part_id: part_id) is None


def test_mate_types_map_to_freecad_joints():
    types = {"REVOLUTE": "Revolute", "SLIDER": "Slider", "CYLINDRICAL": "Cylindrical", "BALL": "Ball", "PARALLEL": "Parallel"}
    for onshape, joint in types.items():
        result = assembly_tasks.spec_from_automate(automate(mate_type=onshape), lambda part_id: part_id)
        assert result["joints"][0]["type"] == joint


def edits():
    base = spec()
    base["joints"].append({"type": "Revolute", "a": "Part2", "b": "Part3", "frame_a": [0, 0, 0, 0, 0, 0, 1], "frame_b": [0, 0, 0, 0, 0, 0, 1]})
    return base


def test_remove_drops_a_leaf_part_and_its_joint_from_the_expected_assembly():
    before, after, text = assembly_tasks.edit_remove(edits())
    assert [p["label"] for p in before["parts"]] == ["Part1", "Part2", "Part3"]
    assert [p["label"] for p in after["parts"]] == ["Part1", "Part2"]
    assert len(after["joints"]) == 1
    assert "Part3" in text


def test_add_is_the_inverse_of_remove_and_states_the_joint_frames():
    before, after, text = assembly_tasks.edit_add(edits())
    assert [p["label"] for p in before["parts"]] == ["Part1", "Part2"]
    assert len(after["parts"]) == 3
    assert "Revolute" in text and "Part3" in text and "Part2" in text


def test_retype_changes_only_the_joint_type():
    before, after, text = assembly_tasks.edit_retype(edits())
    assert before["joints"][1]["type"] == "Revolute"
    assert after["joints"][1] == {**before["joints"][1], "type": "Fixed"}
    assert after["joints"][0] == before["joints"][0]
    assert "Revolute" in text and "Fixed" in text


def test_retype_needs_a_joint_that_is_not_fixed_already():
    assert assembly_tasks.edit_retype(spec()) is None


def test_graph_has_parts_as_nodes_and_joints_as_edges():
    info = {"Part1": {"size": [1, 2, 3], "volume": 6.0, "faces": 6}, "Part2": {"size": [1, 1, 1], "volume": 1.0, "faces": 6}, "Part3": {"size": [1, 1, 1], "volume": 1.0, "faces": 6}}
    graph = assembly_tasks.graph(edits(), info)
    assert [n["name"] for n in graph["nodes"]] == ["Part1", "Part2", "Part3"]
    assert graph["nodes"][0]["size_mm"] == [1, 2, 3]
    assert graph["edges"][1] == {"a": "Part2", "b": "Part3", "type": "Revolute"}
    assert graph["fixed"] == "Part1"


def test_split_is_seeded_70_30_per_mode():
    names = [f"t{i:02d}" for i in range(20)]
    test, validation = assembly_tasks.split(names)
    assert (len(test), len(validation)) == (14, 6)
    assert not set(test) & set(validation)
    assert (test, validation) == assembly_tasks.split(names)


def test_assign_gives_every_mode_assemblies_from_one_pool_without_overlap():
    def entry(n):
        measured = {"parts": {"Part1": {"faces": 10}, "Part2": {"faces": 10}}}
        return f"a{n}", edits(), measured

    chosen = assembly_tasks.assign([entry(n) for n in range(60)])
    ids = [e[0] for e in chosen["from_parts"]] + [e[0] for e in chosen["from_scratch"]] + [e[1][0] for e in chosen["edit"]]
    assert len(ids) == len(set(ids)) == 60
    assert all(len(chosen[mode]) == 20 for mode in assembly_tasks.MODES)


def test_assign_prefers_assemblies_with_a_joint_that_is_not_fixed():
    def entry(n, kind):
        base = edits() if kind == "moving" else spec()
        measured = {"parts": {"Part1": {"faces": 10}, "Part2": {"faces": 10}}}
        return f"{kind}{n}", base, measured

    pool = [entry(n, "fixed") for n in range(10)] + [entry(n, "moving") for n in range(60)]
    chosen = assembly_tasks.assign(pool)
    ids = [e[0] for e in chosen["from_parts"]] + [e[0] for e in chosen["from_scratch"]] + [e[1][0] for e in chosen["edit"]]
    assert len(ids) == 60
    assert not [i for i in ids if i.startswith("fixed")]


def test_task_meta_names_parts_relative_to_the_task_directory():
    relative = assembly_tasks.relative_spec(spec())
    assert [p["step"] for p in relative["parts"]] == ["reference_parts/Part1.step", "reference_parts/Part2.step", "reference_parts/Part3.step"]
