"""
Builds the Assembly benchmark in test-data/assembly/{from_parts,from_scratch,edit}/<task>/ from
AutoMate (CC0, Zenodo record 7776208), read with ranged requests so the 13 GB step.zip is never
downloaded whole. Every task keeps its inputs, a reference.FCStd with joints that solve to 0 and
a task.json; split.json is the seeded 70:30 test:validation split per mode. Run it with
`harness/.venv/bin/python harness/assembly_tasks.py`, never FreeCAD's own Python: the documents are
built and measured in `freecadcmd` through assembly_score.
"""
import argparse
import io
import itertools
import json
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

import assembly_score
import make_sheets

OUT = Path(__file__).resolve().parents[1] / "test-data" / "assembly"
CACHE = Path.home() / ".cache" / "automate"
ZENODO = "https://zenodo.org/api/records/7776208/files/{}/content"
MODES = ("from_parts", "from_scratch", "edit")
JOINT_TYPES = {
    "FASTENED": "Fixed", "REVOLUTE": "Revolute", "SLIDER": "Slider",
    "CYLINDRICAL": "Cylindrical", "BALL": "Ball", "PARALLEL": "Parallel",
}
TASKS_PER_MODE = 20
SEED = 0
MAX_FACES = {"from_parts": 300, "from_scratch": 40, "edit": 300}
MAX_INTERFERENCE = 1e-6
BLOCK = 256 << 10
RETRIES = 15
WORKERS = 6
EDITS = ("remove", "add", "retype")
ASSERT_TOOL = Path(__file__).resolve().parents[1] / "src" / "tools" / "assert_assembly.py"


def matrix_pose(flat, scale=1.0):
    """The x y z qx qy qz qw of a row-major 4x4 transform, translation multiplied by scale."""
    m = [flat[0:3], flat[4:7], flat[8:11]]
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        s = 2 * math.sqrt(trace + 1)
        w, x, y, z = s / 4, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s
    elif m[0][0] >= m[1][1] and m[0][0] >= m[2][2]:
        s = 2 * math.sqrt(1 + m[0][0] - m[1][1] - m[2][2])
        w, x, y, z = (m[2][1] - m[1][2]) / s, s / 4, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s
    elif m[1][1] >= m[2][2]:
        s = 2 * math.sqrt(1 + m[1][1] - m[0][0] - m[2][2])
        w, x, y, z = (m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, s / 4, (m[1][2] + m[2][1]) / s
    else:
        s = 2 * math.sqrt(1 + m[2][2] - m[0][0] - m[1][1])
        w, x, y, z = (m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, s / 4
    return [flat[3] * scale, flat[7] * scale, flat[11] * scale, x, y, z, w]


def spec_from_automate(assembly, step_path):
    """Assembly spec in mm from an AutoMate assembly JSON, or None when a mate has no FreeCAD joint."""
    if assembly["subAssemblies"] or assembly["mateRelations"] or assembly["mateGroups"]:
        return None
    if any(m["mateType"] not in JOINT_TYPES or m["occurrences"][0] == m["occurrences"][1] for m in assembly["mates"]):
        return None
    labels = [f"Part{i + 1}" for i in range(len(assembly["occurrences"]))]
    parts = [
        {
            "label": label,
            "step": step_path(assembly["parts"][occurrence["part"]]["id"]),
            "placement": matrix_pose(occurrence["transform"], 1000.0),
        }
        for label, occurrence in zip(labels, assembly["occurrences"])
    ]
    joints = [
        {
            "type": JOINT_TYPES[mate["mateType"]],
            "a": labels[mate["occurrences"][0]],
            "b": labels[mate["occurrences"][1]],
            "frame_a": matrix_pose(mate["mcfs"][0], 1000.0),
            "frame_b": matrix_pose(mate["mcfs"][1], 1000.0),
        }
        for mate in assembly["mates"]
    ]
    fixed = [label for label, occurrence in zip(labels, assembly["occurrences"]) if occurrence["fixed"]]
    return {"parts": parts, "joints": joints, "ground": (fixed or labels)[0]}


def _frame(pose):
    return "position (%.3f, %.3f, %.3f) mm, rotation quaternion (%.4f, %.4f, %.4f, %.4f)" % tuple(pose)


def describe_joint(joint):
    return (
        f"a {joint['type']} joint between {joint['a']} (connector frame at {_frame(joint['frame_a'])}) "
        f"and {joint['b']} (connector frame at {_frame(joint['frame_b'])})"
    )


def _without(spec, label):
    return {
        "parts": [p for p in spec["parts"] if p["label"] != label],
        "joints": [j for j in spec["joints"] if label not in (j["a"], j["b"])],
        "ground": spec["ground"],
    }


def _reached(spec, start, skip=None):
    """Labels joined to *start* by a chain of joints, leaving out the joint at index *skip*."""
    reached, spread = {start}, True
    while spread:
        spread = False
        for index, joint in enumerate(spec["joints"]):
            if index != skip and (joint["a"] in reached) != (joint["b"] in reached):
                reached |= {joint["a"], joint["b"]}
                spread = True
    return reached


def joined_to_ground(spec):
    return _reached(spec, spec["ground"]) == {p["label"] for p in spec["parts"]}


def _leaf(spec):
    """The last part that hangs off the assembly by one joint, in an assembly whose parts all reach the ground and which stays so without it."""
    if not joined_to_ground(spec):
        return None
    leaves = [
        p["label"] for p in spec["parts"]
        if p["label"] != spec["ground"] and sum(p["label"] in (j["a"], j["b"]) for j in spec["joints"]) == 1
        and joined_to_ground(_without(spec, p["label"]))
    ]
    return leaves[-1] if leaves else None


def edit_remove(spec):
    """(before, after, text) removing the last part that hangs off the assembly by one joint; a part whose removal strands another is never offered."""
    label = _leaf(spec)
    if label is None:
        return None
    return spec, _without(spec, label), f"Remove {label} and its joint from the assembly."


def edit_add(spec):
    """(before, after, text) adding back a part the before assembly lacks, with its joint stated."""
    label = _leaf(spec)
    if label is None:
        return None
    joint = next(j for j in spec["joints"] if label in (j["a"], j["b"]))
    text = f"Add {label} (parts/{label}.step) to the assembly and join it with {describe_joint(joint)}."
    return _without(spec, label), spec, text


def _multiply(one, other):
    x1, y1, z1, a1, b1, c1, d1 = one
    x2, y2, z2, a2, b2, c2, d2 = other
    return [
        x1 + (1 - 2 * (b1 * b1 + c1 * c1)) * x2 + 2 * (a1 * b1 - c1 * d1) * y2 + 2 * (a1 * c1 + b1 * d1) * z2,
        y1 + 2 * (a1 * b1 + c1 * d1) * x2 + (1 - 2 * (a1 * a1 + c1 * c1)) * y2 + 2 * (b1 * c1 - a1 * d1) * z2,
        z1 + 2 * (a1 * c1 - b1 * d1) * x2 + 2 * (b1 * c1 + a1 * d1) * y2 + (1 - 2 * (a1 * a1 + b1 * b1)) * z2,
        d1 * a2 + a1 * d2 + b1 * c2 - c1 * b2,
        d1 * b2 - a1 * c2 + b1 * d2 + c1 * a2,
        d1 * c2 + a1 * b2 - b1 * a2 + c1 * d2,
        d1 * d2 - a1 * a2 - b1 * b2 - c1 * c2,
    ]


def _inverse(pose):
    x, y, z, a, b, c, d = pose
    turned = _multiply([0, 0, 0, -a, -b, -c, d], [-x, -y, -z, 0, 0, 0, 1])
    return [*turned[:3], -a, -b, -c, d]


def edit_retype(spec):
    """(before, after, text) turning the first joint that is not Fixed into a Fixed one that can be re-solved.

    The part on the far side of the joint from the ground, and every part hanging off it, move together so that the two connector frames meet and keep their own joints; a joint on a loop cannot be re-solved and is skipped.
    """
    if not joined_to_ground(spec):
        return None
    for index, joint in enumerate(spec["joints"]):
        if joint["type"] == "Fixed":
            continue
        near = _reached(spec, spec["ground"], skip=index)
        if joint["a"] in near and joint["b"] in near:
            continue
        anchor, mover = ("a", "b") if joint["a"] in near else ("b", "a")
        placements = {p["label"]: p["placement"] for p in spec["parts"]}
        target = _multiply(_multiply(placements[joint[anchor]], joint["frame_" + anchor]), _inverse(joint["frame_" + mover]))
        delta = _multiply(target, _inverse(placements[joint[mover]]))
        moving = _reached(spec, joint[mover], skip=index)
        parts = [{**p, "placement": _multiply(delta, p["placement"]) if p["label"] in moving else p["placement"]} for p in spec["parts"]]
        joints = [{**j, "type": "Fixed"} if i == index else j for i, j in enumerate(spec["joints"])]
        text = f"Change the {joint['type']} joint between {joint['a']} and {joint['b']} to a Fixed joint."
        return spec, {**spec, "parts": parts, "joints": joints}, text
    return None


EDIT_FUNCTIONS = {"remove": edit_remove, "add": edit_add, "retype": edit_retype}


def graph(spec, info):
    """MUSE-style graph: parts as nodes with size, volume and face count, joints as typed edges."""
    return {
        "nodes": [
            {"name": p["label"], "size_mm": info[p["label"]]["size"], "volume_mm3": info[p["label"]]["volume"], "faces": info[p["label"]]["faces"]}
            for p in spec["parts"]
        ],
        "edges": [{"a": j["a"], "b": j["b"], "type": j["type"]} for j in spec["joints"]],
        "fixed": spec["ground"],
    }


def split(names):
    return make_sheets.split(names, make_sheets.test_count(len(names)), SEED)


def split_edits(kinds):
    """(test, validation) of the edit tasks in *kinds* (name to kind): the 70:30 split is made inside each kind, so every kind has tasks in test; the largest remainders take the rounding."""
    groups = {kind: sorted(n for n, k in kinds.items() if k == kind) for kind in EDITS}
    shares = {kind: make_sheets.TEST_SHARE * len(names) for kind, names in groups.items()}
    counts = {kind: int(share) for kind, share in shares.items()}
    spare = make_sheets.test_count(len(kinds)) - sum(counts.values())
    for kind in sorted(EDITS, key=lambda k: shares[k] - counts[k], reverse=True)[:spare]:
        counts[kind] += 1
    test = {n for kind, names in groups.items() for n in make_sheets.split(names, counts[kind], SEED)[0]}
    return sorted(test), sorted(set(kinds) - test)


def project_python(root=None):
    """The interpreter that has the engine: DAE_PYTHON, else the .venv of the main checkout of the repository at *root*, which a git worktree does not carry; FileNotFoundError names DAE_PYTHON where there is none."""
    if os.environ.get("DAE_PYTHON"):
        return Path(os.environ["DAE_PYTHON"])
    root = Path(root or Path(__file__).resolve().parents[1])
    common = subprocess.run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True, check=True).stdout.strip()
    python = Path(common).parent / ".venv" / "bin" / "python"
    if not python.exists():
        raise FileNotFoundError(f"{python} does not exist: set DAE_PYTHON to the interpreter that runs the engine")
    return python


def assertion(paths):
    """Why each saved Document in *paths* fails the Assembly Assertion, or None where it holds; run by the project interpreter DAE_PYTHON, which has the engine."""
    run = subprocess.run([str(project_python()), str(ASSERT_TOOL), *map(str, paths)], capture_output=True, text=True)
    verdicts = {}
    for line in run.stdout.splitlines():
        path, _, verdict = line.partition(": ")
        verdicts[path] = None if verdict == "pass" else verdict
    return {str(path): verdicts[str(path)] for path in paths}


def assertion_passes(options):
    """The (assembly id, kind) of the *options* (id, kind, before, after) whose input and reference both hold the Assertion."""
    work = Path(tempfile.mkdtemp(prefix="assembly-assert-"))
    jobs, names = [], []
    for number, (assembly_id, kind, before, after) in enumerate(options):
        for role, spec in (("input", before), ("reference", after)):
            jobs.append((spec, work / f"{number}_{role}.FCStd"))
        names.append((assembly_id, kind, jobs[-2][1], jobs[-1][1]))
    assembly_score.build(jobs)
    verdicts = assertion([path for _, _, one, other in names for path in (one, other)])
    shutil.rmtree(work)
    return {(i, k) for i, k, one, other in names if verdicts[str(one)] is None and verdicts[str(other)] is None}


def _substantial(kind, spec):
    """Whether the edit leaves more than a lone part: a remove keeps two parts and a joint."""
    edit = EDIT_FUNCTIONS[kind](spec)
    return kind != "remove" or (edit is not None and len(edit[1]["parts"]) >= 2 and bool(edit[1]["joints"]))


def pick_edits(pool, validate=None):
    """About TASKS_PER_MODE (kind, entry) edits from distinct assemblies of *pool*, kinds balanced, retype first because it needs a joint that is not Fixed; ValueError where a kind falls short."""
    options = [
        (entry[0], kind, *EDIT_FUNCTIONS[kind](entry[1])[:2])
        for entry in pool for kind in EDITS if EDIT_FUNCTIONS[kind](entry[1])
    ]
    allowed = validate(options) if validate else {(i, k) for i, k, _, _ in options}
    quota = {kind: TASKS_PER_MODE // 3 + (index < TASKS_PER_MODE % 3) for index, kind in enumerate(("retype", "remove", "add"))}
    taken, chosen = set(), {kind: [] for kind in EDITS}
    for kind in ("retype", "remove", "add"):
        for entry in sorted(pool, key=lambda e: not _substantial(kind, e[1])):
            if len(chosen[kind]) < quota[kind] and entry[0] not in taken and (entry[0], kind) in allowed:
                chosen[kind].append((kind, entry))
                taken.add(entry[0])
        if len(chosen[kind]) < quota[kind]:
            raise ValueError(f"{len(chosen[kind])} valid {kind} edit tasks in the pool, {quota[kind]} wanted")
    return [item for group in itertools.zip_longest(*chosen.values()) for item in group if item]


class RangeFile(io.RawIOBase):
    """Read-only seekable view of a Zenodo file through HTTP range requests."""

    def __init__(self, name):
        self.name = name
        self.url = ZENODO.format(name)
        self.client = httpx.Client(follow_redirects=True, timeout=300)
        sizes = CACHE / "blocks" / f"{name}.size"
        if not sizes.exists():
            sizes.parent.mkdir(parents=True, exist_ok=True)
            while True:
                probe = self.client.get(self.url, headers={"Range": "bytes=0-0"})
                if "content-range" in probe.headers:
                    break
                time.sleep(int(probe.headers.get("retry-after", 30)))
            sizes.write_text(probe.headers["content-range"].split("/")[1])
        self.size = int(sizes.read_text())
        self.position = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        self.position = (offset, self.position + offset, self.size + offset)[whence]
        return self.position

    def _block(self, index):
        path = CACHE / "blocks" / self.name / f"{index}"
        if not path.exists():
            start = index * BLOCK
            end = min(self.size, start + BLOCK) - 1
            for attempt in range(RETRIES):
                try:
                    response = self.client.get(self.url, headers={"Range": f"bytes={start}-{end}"})
                    if response.status_code == 429:
                        time.sleep(int(response.headers.get("retry-after", 60)))
                    elif len(response.content) == end - start + 1:
                        data = response.content
                        break
                except httpx.HTTPError:
                    time.sleep(min(30, 3 * attempt))
            else:
                raise OSError(f"{self.name}: bytes {start}-{end} unreachable")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return path.read_bytes()

    def read(self, size=-1):
        end = self.size if size < 0 else min(self.size, self.position + size)
        chunks = []
        while self.position < end:
            index, offset = divmod(self.position, BLOCK)
            piece = self._block(index)[offset:end - index * BLOCK]
            chunks.append(piece)
            self.position += len(piece)
        return b"".join(chunks)


HEADER = re.compile(rb"PK\x03\x04.{26}(?:step|assemblies)/", re.DOTALL)
PROBE = 256 << 10


def _first_header(file, offset):
    while offset < file.size:
        file.seek(offset)
        window = file.read(PROBE)
        found = HEADER.search(window)
        if found:
            start = found.start()
            name_length = struct.unpack("<H", window[start + 26:start + 28])[0]
            return offset + start, window[start + 30:start + 30 + name_length].decode()
        offset += PROBE - 64
    return None


def _member(archive, name):
    """One member of a Zenodo zip: its entries are sorted by name, so a bisection over local headers finds it without the central directory."""
    file = RangeFile(archive)
    low, high = 0, file.size
    while high - low > PROBE:
        middle = (low + high) // 2
        header = _first_header(file, middle)
        if header is not None and header[1] == name:
            low = high = header[0]
        elif header is None or header[1] > name or header[0] >= high:
            high = middle
        else:
            low = header[0] + 1
    header = _first_header(file, low)
    while header and header[1] != name:
        header = _first_header(file, header[0] + 1)
        if header and header[1] > name:
            raise KeyError(name)
    if header is None:
        raise KeyError(name)
    file.seek(header[0])
    fixed = file.read(30)
    method, compressed = struct.unpack("<H", fixed[8:10])[0], struct.unpack("<I", fixed[18:22])[0]
    name_length, extra_length = struct.unpack("<HH", fixed[26:30])
    file.seek(header[0] + 30 + name_length + extra_length)
    data = file.read(compressed)
    return zlib.decompress(data, -15) if method == 8 else data


def assembly_json(assembly_id):
    path = CACHE / "assemblies" / f"{assembly_id}.json"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_member("assemblies.zip", f"assemblies/{assembly_id}.json"))
    return json.loads(path.read_text())


def step_file(part_id):
    path = CACHE / "step" / f"{part_id}.step"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_member("step.zip", f"step/{part_id}.step"))
    return str(path)


def select_candidates(parquet, out, count=1500):
    """Seeded shuffle of the 2-4 part, STEP-complete assemblies with plain mates; needs pandas and pyarrow."""
    import pandas
    import random

    table = pandas.read_parquet(parquet)
    plain = table[
        (table.n_step == table.n_parts) & (table.n_subassemblies == 0) & table.n_parts.between(2, 4)
        & (table.n_occurrences <= 6) & table.n_mates.between(1, 6) & (table.n_step_mates == table.n_mates)
        & (table.n_relations == 0) & (table.n_groups == 0)
    ]
    ids = sorted(plain.assemblyId)
    random.Random(SEED).shuffle(ids)
    Path(out).write_text(json.dumps(ids[:count]))


def _usable(measured, mode_faces=MAX_FACES["from_parts"]):
    volume = sum(p["volume"] for p in measured["parts"].values())
    return (
        measured["solve"] == 0
        and all(p["volume"] > 0 for p in measured["parts"].values())
        and measured["interference"] <= MAX_INTERFERENCE * volume
        and max(p["faces"] for p in measured["parts"].values()) <= mode_faces
    )


def _parallel(function, items):
    with ThreadPoolExecutor(WORKERS) as pool:
        return list(pool.map(function, items))


def gather(wanted, batch=24, cached_only=False):
    """Specs of AutoMate assemblies whose reference solves to 0 with no interference, with their measurements; cached_only reads no candidate that is not in the local cache."""
    ids = json.loads((CACHE / "candidates.json").read_text())
    if cached_only:
        ids = [i for i in ids if (CACHE / "assemblies" / f"{i}.json").exists()]
    found = []
    work = Path(tempfile.mkdtemp(prefix="assembly-gather-"))
    for start in range(0, len(ids), batch):
        assemblies = _parallel(assembly_json, ids[start:start + batch])
        mapped = [a for a in assemblies if spec_from_automate(a, lambda part_id: part_id) is not None]
        _parallel(step_file, sorted({p["id"] for a in mapped for p in a["parts"]}))
        entries = [(a["assemblyId"], spec_from_automate(a, step_file)) for a in mapped]
        paths = [work / f"{assembly_id}.FCStd" for assembly_id, _ in entries]
        assembly_score.build([(spec, path) for (_, spec), path in zip(entries, paths)])
        for (assembly_id, spec), measured in zip(entries, assembly_score.measure(paths)):
            if _usable(measured):
                found.append((assembly_id, spec, measured))
        print(f"{start + batch} candidates read, {len(found)} usable", flush=True)
        if len(found) >= wanted:
            break
    shutil.rmtree(work)
    return found


def _faces(measured):
    return max(p["faces"] for p in measured["parts"].values())


def _moves(entry):
    return any(j["type"] != "Fixed" for j in entry[1]["joints"])


def assign(found, validate=None):
    """Distinct usable assemblies per mode, those with a joint that is not Fixed first: simple parts to from_scratch, the rest dealt alternately to a reserve and from_parts, and the edit tasks drawn from the reserve and what is left over with `pick_edits`."""
    found = sorted(found, key=lambda entry: not _moves(entry))
    scratch = [f for f in found if _faces(f[2]) <= MAX_FACES["from_scratch"] and len(f[1]["parts"]) <= 3][:TASKS_PER_MODE]
    taken = {f[0] for f in scratch}
    edits, parts = [], []
    for entry in (f for f in found if f[0] not in taken):
        order = EDITS[len(edits) % 3:] + EDITS[:len(edits) % 3]
        kind = next((k for k in order if EDIT_FUNCTIONS[k](entry[1])), None)
        wants_edit = len(edits) <= len(parts) or len(parts) >= TASKS_PER_MODE
        if kind and wants_edit and len(edits) < TASKS_PER_MODE:
            edits.append((kind, entry))
        elif len(parts) < TASKS_PER_MODE:
            parts.append(entry)
    used = taken | {f[0] for f in parts}
    return {"from_scratch": scratch, "edit": pick_edits([f for f in found if f[0] not in used], validate), "from_parts": parts}


def _size(part):
    box = part["bbox"]
    return [round(box[i + 3] - box[i], 3) for i in range(3)]


def relative_spec(spec):
    """The spec with every part STEP named reference_parts/<label>.step, relative to the task directory."""
    return {**spec, "parts": [{**p, "step": f"reference_parts/{p['label']}.step"} for p in spec["parts"] if "step" in p]}


def _write_task(directory, meta, documents, step_labels):
    directory.mkdir(parents=True)
    for part in meta["reference_spec"]["parts"]:
        (directory / "reference_parts").mkdir(exist_ok=True)
        shutil.copy(part["step"], directory / "reference_parts" / f"{part['label']}.step")
    meta["reference_spec"] = relative_spec(meta["reference_spec"])
    for label, source in step_labels.items():
        (directory / "parts").mkdir(exist_ok=True)
        shutil.copy(source, directory / "parts" / f"{label}.step")
    assembly_score.build([(spec, directory / name) for name, spec in documents.items()])
    (directory / "task.json").write_text(json.dumps(meta, indent=1))


def _meta(mode, name, assembly_id, text, inputs, spec):
    return {
        "mode": mode, "id": name, "source": "AutoMate", "source_id": assembly_id, "licence": "CC0-1.0",
        "text": text, "inputs": inputs, "reference_spec": spec,
    }


NOT_USED = '''
## Checked, not used as sources

- MUSE (arXiv 2605.28579): dataset CC BY 4.0 (Hugging Face card dongxiaoyu/MUSE and project page), code MIT (github.com/dong7313/muse). Usable; only its Physical Assembly Graph idea is used here: from_scratch tasks carry a graph of parts as nodes and joints as edges. No MUSE data is copied.
- IndustrialCAD-1400 (arXiv 2610.24789): the paper is CC BY 4.0; the dataset states no licence and no public location, its inputs are text requests scored from rendered views. Left out.
- MSD (CC BY-SA 4.0): Swiss dwelling floor plans, no mechanical parts or joints. Left out of the Assembly set.
'''


def write_credits(out, names):
    rows = ["| task | source | licence | AutoMate assembly |", "| --- | --- | --- | --- |"]
    for mode in MODES:
        for name in names[mode]:
            meta = json.loads((out / mode / name / "task.json").read_text())
            rows.append(f"| {name} | AutoMate (https://doi.org/10.5061/dryad.2547d7wvw) | {meta['licence']} | {meta['source_id']} |")
    (out / "CREDITS.md").write_text("# Credits\n\n" + "\n".join(rows) + "\n" + NOT_USED)


def write_edits(edits, out):
    """Write the edit tasks of *edits* ((kind, entry) pairs) in *out*/edit and return their names."""
    names = []
    for number, (kind, (assembly_id, spec, measured)) in enumerate(edits, 1):
        name = f"edit_{number:02d}"
        before, after, text = EDIT_FUNCTIONS[kind](spec)
        added = {p["label"]: p["step"] for p in spec["parts"] if kind == "add" and p["label"] not in {q["label"] for q in before["parts"]}}
        meta = _meta("edit", name, assembly_id, text, {"input": "input.FCStd", "edit": kind}, after)
        _write_task(out / "edit" / name, meta, {"input.FCStd": before, "reference.FCStd": after}, added)
        names.append(name)
    return names


def write_edits_only(edits, out=OUT):
    """Replace the edit tasks, their split and the credits in the benchmark under *out*, leaving the other modes as they are."""
    if (out / "edit").exists():
        shutil.rmtree(out / "edit")
    names = {mode: sorted(p.name for p in (out / mode).iterdir()) for mode in MODES if mode != "edit"}
    names["edit"] = write_edits(edits, out)
    kinds = {n: json.loads((out / "edit" / n / "task.json").read_text())["inputs"]["edit"] for n in names["edit"]}
    test, validation = split_edits(kinds)
    splits = json.loads((out / "split.json").read_text())
    splits["edit"] = {"test": test, "validation": validation}
    (out / "split.json").write_text(json.dumps(splits, indent=1))
    write_credits(out, names)


def write_tasks(chosen, out=OUT):
    if out.exists():
        shutil.rmtree(out)
    names = {mode: [] for mode in MODES}
    for number, (assembly_id, spec, measured) in enumerate(chosen["from_parts"], 1):
        name = f"from_parts_{number:02d}"
        joints = "; ".join(describe_joint(j) for j in spec["joints"])
        text = (
            f"Create one Assembly from the {len(spec['parts'])} part files in parts/ (each file is one part; "
            f"Part names are the file names). {spec['ground']} is grounded. Join the parts with native joints: {joints}."
        )
        inputs = {"parts": [{"label": p["label"], "file": f"parts/{p['label']}.step"} for p in spec["parts"]], "ground": spec["ground"]}
        steps = {p["label"]: p["step"] for p in spec["parts"]}
        _write_task(out / "from_parts" / name, _meta("from_parts", name, assembly_id, text, inputs, spec), {"reference.FCStd": spec}, steps)
        names["from_parts"].append(name)
    for number, (assembly_id, spec, measured) in enumerate(chosen["from_scratch"], 1):
        name = f"from_scratch_{number:02d}"
        nodes = graph(spec, {l: {"size": _size(p), "volume": round(p["volume"], 3), "faces": p["faces"]} for l, p in measured["parts"].items()})
        for node in nodes["nodes"]:
            node["bbox_mm"] = [round(v, 3) for v in measured["parts"][node["name"]]["bbox"]]
        text = (
            "Model every part of the graph as its own Body with the given size, volume and face count, then join "
            "the parts in one Assembly with native joints of the edge types, so that each part sits in its bbox_mm "
            f"in the solved assembly. {spec['ground']} is grounded."
        )
        _write_task(out / "from_scratch" / name, _meta("from_scratch", name, assembly_id, text, {"graph": nodes}, spec), {"reference.FCStd": spec}, {})
        names["from_scratch"].append(name)
    names["edit"] = write_edits(chosen["edit"], out)
    splits = {}
    for mode, listed in names.items():
        if mode == "edit":
            test, validation = split_edits({n: json.loads((out / mode / n / "task.json").read_text())["inputs"]["edit"] for n in listed})
        else:
            test, validation = split(listed)
        splits[mode] = {"test": test, "validation": validation}
    (out / "split.json").write_text(json.dumps({"seed": SEED, **splits}, indent=1))
    write_credits(out, names)
    return names


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["candidates", "build", "edits"])
    parser.add_argument("--parquet", default=str(OUT / "_src" / "assemblies.parquet"))
    parser.add_argument("--cached", action="store_true")
    parser.add_argument("--wanted", type=int, default=4 * TASKS_PER_MODE)
    args = parser.parse_args()
    if args.command == "candidates":
        select_candidates(args.parquet, CACHE / "candidates.json")
        return
    if args.command == "edits":
        taken = {json.loads(p.read_text())["source_id"] for mode in ("from_parts", "from_scratch") for p in (OUT / mode).glob("*/task.json")}
        found = gather(args.wanted, cached_only=args.cached)
        write_edits_only(pick_edits([f for f in found if f[0] not in taken], assertion_passes))
        return
    src = OUT / "_src"
    kept = src.exists()
    found = gather(args.wanted, cached_only=args.cached)
    chosen = assign(found, assertion_passes)
    if kept:
        shutil.move(src, CACHE / "_src_keep")
    write_tasks(chosen)
    if kept:
        shutil.move(CACHE / "_src_keep", src)
    print({mode: len(items) for mode, items in chosen.items()})


if __name__ == "__main__":
    main()
