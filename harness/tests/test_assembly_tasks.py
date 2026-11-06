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


def pose_of(spec_, label):
    return next(p["placement"] for p in spec_["parts"] if p["label"] == label)


def chain():
    ident = [0, 0, 0, 0, 0, 0, 1]
    turned = [0, 0, 0, 0, 0, math.sqrt(0.5), math.sqrt(0.5)]
    parts = [
        {"label": "Part1", "step": "a", "placement": [0, 0, 0, 0, 0, 0, 1]},
        {"label": "Part2", "step": "b", "placement": [100, 0, 0, *turned[3:]]},
        {"label": "Part3", "step": "c", "placement": [200, 0, 0, 0, 0, 0, 1]},
        {"label": "Part4", "step": "d", "placement": [230, 0, 0, 0, 0, 0, 1]},
    ]
    joints = [
        {"type": "Fixed", "a": "Part1", "b": "Part2", "frame_a": ident, "frame_b": ident},
        {"type": "Revolute", "a": "Part2", "b": "Part3", "frame_a": ident, "frame_b": ident},
        {"type": "Fixed", "a": "Part3", "b": "Part4", "frame_a": [30, 0, 0, 0, 0, 0, 1], "frame_b": ident},
    ]
    return {"parts": parts, "joints": joints, "ground": "Part1"}


def stranded():
    base = chain()
    base["joints"] = [base["joints"][0], base["joints"][2]]
    return base


def test_no_edit_is_offered_on_an_assembly_with_parts_not_joined_to_ground():
    for function in assembly_tasks.EDIT_FUNCTIONS.values():
        assert function(stranded()) is None


def test_remove_takes_a_leaf_and_leaves_every_other_part_joined_to_ground():
    before, after, _ = assembly_tasks.edit_remove(chain())
    assert [p["label"] for p in after["parts"]] == ["Part1", "Part2", "Part3"]
    assert assembly_tasks.joined_to_ground(after)


def test_retype_moves_the_part_to_meet_the_fixed_frames_and_carries_what_hangs_off_it():
    before, after, _ = assembly_tasks.edit_retype(chain())
    assert pose_of(after, "Part3")[:3] == pose_of(after, "Part2")[:3] == [100, 0, 0]
    assert all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(pose_of(after, "Part3")[3:], pose_of(after, "Part2")[3:]))
    assert pose_of(after, "Part1") == pose_of(before, "Part1")
    assert pose_of(after, "Part2") == pose_of(before, "Part2")
    assert math.isclose(pose_of(after, "Part4")[0], 100.0, abs_tol=1e-9) and math.isclose(pose_of(after, "Part4")[1], 30.0, abs_tol=1e-9)


def test_retype_is_not_offered_on_a_joint_that_closes_a_loop():
    base = chain()
    base["parts"] = base["parts"][:3]
    ident = [0, 0, 0, 0, 0, 0, 1]
    base["joints"] = [base["joints"][1], *[{**j, "type": "Revolute"} for j in (
        {"a": "Part1", "b": "Part2", "frame_a": ident, "frame_b": ident},
        {"a": "Part1", "b": "Part3", "frame_a": ident, "frame_b": ident},
    )]]
    assert assembly_tasks.edit_retype(base) is None


def test_edit_split_keeps_70_30_and_at_least_four_retypes_in_test():
    kinds = {f"edit_{n:02d}": ("retype", "remove", "add")[n % 3] for n in range(20)}
    test, validation = assembly_tasks.split_edits(kinds)
    assert (len(test), len(validation)) == (14, 6)
    assert not set(test) & set(validation)
    assert sum(kinds[n] == "retype" for n in test) >= 4
    assert all(sum(kinds[n] == kind for n in test) >= 4 for kind in ("retype", "remove", "add"))


def pool(n):
    measured = {"parts": {"Part1": {"faces": 10}, "Part2": {"faces": 10}}}
    return [(f"a{i}", chain(), measured) for i in range(n)]


def test_edits_are_balanced_across_the_three_kinds():
    chosen = assembly_tasks.assign(pool(60))["edit"]
    kinds = [k for k, _ in chosen]
    assert len(chosen) == 20
    assert sorted(kinds.count(k) for k in assembly_tasks.EDITS) == [6, 7, 7]


def test_an_assembly_whose_documents_fail_the_assertion_is_not_drawn():
    refused = {"a0", "a1", "a2"}
    validate = lambda options: {(i, k) for i, k, _, _ in options if i not in refused}
    chosen = assembly_tasks.assign(pool(60), validate)["edit"]
    assert len(chosen) == 20
    assert not refused & {entry[0] for _, entry in chosen}


def test_the_draw_stops_when_the_pool_cannot_yield_the_quota():
    import pytest

    with pytest.raises(ValueError):
        assembly_tasks.assign(pool(30))


def test_removes_keep_at_least_two_parts_and_a_joint_while_the_pool_has_such_options():
    ident = [0, 0, 0, 0, 0, 0, 1]

    def entry(n, parts):
        labels = [f"Part{i + 1}" for i in range(parts)]
        spec_ = {
            "parts": [{"label": l, "step": l, "placement": [10 * i, 0, 0, 0, 0, 0, 1]} for i, l in enumerate(labels)],
            "joints": [{"type": "Revolute", "a": labels[i], "b": labels[i + 1], "frame_a": ident, "frame_b": ident} for i in range(parts - 1)],
            "ground": "Part1",
        }
        return f"a{n}", spec_, {"parts": {"Part1": {"faces": 10}}}

    small = [entry(f"s{n}", 2) for n in range(30)]
    large = [entry(f"l{n}", 3) for n in range(20)]
    chosen = assembly_tasks.pick_edits(small + large)
    removes = [e for k, e in chosen if k == "remove"]
    assert removes and all(len(assembly_tasks.edit_remove(e[1])[1]["parts"]) >= 2 for e in removes)


def test_project_python_is_resolved_through_the_main_checkout_of_a_worktree(tmp_path):
    import subprocess

    import pytest

    main = tmp_path / "main"
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    subprocess.run(["git", "-C", str(main), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(tmp_path / "wt")], check=True)
    with pytest.raises(FileNotFoundError, match="DAE_PYTHON"):
        assembly_tasks.project_python(tmp_path / "wt")
    (main / ".venv" / "bin").mkdir(parents=True)
    (main / ".venv" / "bin" / "python").write_text("")
    assert assembly_tasks.project_python(tmp_path / "wt") == (main / ".venv" / "bin" / "python").resolve()

