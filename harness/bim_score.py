"""
Scores a candidate IFC4 file against a reference one, deterministically; 0 when the candidate does not open. Otherwise the walls
component times the mean of five others, all in [0, 1]. Walls: length, height and thickness, times how near the centre is
(zero at 0.25 m). Others: counts per IfcType, space areas, door and window hosting, their size, and room adjacency.
Elements match greedily by distance, nearest first.
"""
from pathlib import Path

import assembly_score

HARNESS = Path(__file__).resolve().parent
SCRIPT = HARNESS / "bim_freecad.py"
TYPES = ("IfcWall", "IfcSlab", "IfcDoor", "IfcWindow", "IfcSpace")
WALL_MATCH = 0.75
WALL_SHIFT = 0.25
SPACE_MATCH = 1.5
OPENING_MATCH = 0.75
LEVEL_MATCH = 0.5
STAIR_MATCH = 0.3
COMPONENTS = ("counts", "walls", "spaces", "hosting", "openings", "adjacency")


def build(jobs):
    """Write each (spec, path) as an Arch document at path and its IFC4 export beside it."""
    assembly_score.run_freecadcmd({"build": [[spec, str(path)] for spec, path in jobs]}, SCRIPT)


def measure(paths):
    """Measure each IFC: counts, wall rectangles, spaces, openings with host wall, adjacency edges."""
    return assembly_score.run_freecadcmd({"measure": [str(path) for path in paths]}, SCRIPT)


def audit(paths):
    """Per IFC: Doors and Windows filling nothing, elements outside a storey, and each Door and Window's attribute over geometry size."""
    return assembly_score.run_freecadcmd({"audit": [str(path) for path in paths]}, SCRIPT)


def _distance(one, other):
    return ((one[0] - other[0]) ** 2 + (one[1] - other[1]) ** 2) ** 0.5


def _nearest(pairs):
    """Greedy nearest-first {reference index: candidate index} of the (distance, reference index, candidate index) *pairs*."""
    found, used = {}, set()
    for _, i, j in sorted(pairs):
        if i not in found and j not in used:
            found[i] = j
            used.add(j)
    return found


def _match(reference, candidate, limit):
    """Greedy nearest-first pairs {reference index: candidate index} of centres within limit."""
    return _nearest(
        (_distance(r, c), i, j) for i, r in enumerate(reference) for j, c in enumerate(candidate) if _distance(r, c) <= limit
    )


def _ratio(candidate, reference):
    top = max(candidate, reference)
    return 1.0 if top == 0 else max(0.0, 1 - abs(candidate - reference) / top)


def _share(total, reference_count, candidate_count):
    most = max(reference_count, candidate_count)
    return 1.0 if most == 0 else total / most


def _counts(candidate, reference):
    return sum(_ratio(candidate["counts"][t], reference["counts"][t]) for t in TYPES) / len(TYPES)


def _walls(candidate, reference, matched):
    total = 0.0
    for i, j in matched.items():
        ref, cand = reference["walls"][i], candidate["walls"][j]
        shift = max(0.0, 1 - _distance(ref["c"], cand["c"]) / WALL_SHIFT)
        total += sum(_ratio(cand[k], ref[k]) for k in ("len", "h", "thk")) / 3 * shift
    return _share(total, len(reference["walls"]), len(candidate["walls"]))


def _spaces(candidate, reference, matched):
    total = sum(_ratio(candidate["spaces"][j]["area"], reference["spaces"][i]["area"]) for i, j in matched.items())
    return _share(total, len(reference["spaces"]), len(candidate["spaces"]))


def _openings(candidate, reference, walls):
    """(hosting, size): matched doors and windows whose host wall matches, and their mean width and height ratio."""
    hosted, sized = 0.0, 0.0
    for kind in ("IfcDoor", "IfcWindow"):
        ref = [o for o in reference["openings"] if o["type"] == kind]
        cand = [o for o in candidate["openings"] if o["type"] == kind]
        for i, j in _match([o["c"] for o in ref], [o["c"] for o in cand], OPENING_MATCH).items():
            hosted += ref[i]["host"] is not None and walls.get(ref[i]["host"]) == cand[j]["host"]
            sized += (_ratio(cand[j]["w"], ref[i]["w"]) + _ratio(cand[j]["h"], ref[i]["h"])) / 2
    count = len(reference["openings"]), len(candidate["openings"])
    return _share(hosted, *count), _share(sized, *count)


def _adjacency(candidate, reference, spaces):
    back = {j: i for i, j in spaces.items()}
    wanted = {tuple(sorted(edge)) for edge in reference["adjacency"]}
    found = {tuple(sorted((back.get(a, -1 - a), back.get(b, -1 - b)))) for a, b in candidate["adjacency"]}
    return 1.0 if not wanted and not found else len(wanted & found) / len(wanted | found)


def combine(candidate, reference):
    """Score two measurements; every component is in [0, 1]."""
    if not candidate["opens"]:
        return {"score": 0.0, **dict.fromkeys(COMPONENTS, 0.0)}
    walls = _match([w["c"] for w in reference["walls"]], [w["c"] for w in candidate["walls"]], WALL_MATCH)
    spaces = _match([s["c"] for s in reference["spaces"]], [s["c"] for s in candidate["spaces"]], SPACE_MATCH)
    hosting, openings = _openings(candidate, reference, walls)
    result = {
        "counts": _counts(candidate, reference),
        "walls": _walls(candidate, reference, walls),
        "spaces": _spaces(candidate, reference, spaces),
        "hosting": hosting,
        "openings": openings,
        "adjacency": _adjacency(candidate, reference, spaces),
    }
    others = [value for key, value in result.items() if key != "walls"]
    return {"score": result["walls"] * sum(others) / len(others), **result}


def score(candidate_path, reference_path):
    candidate, reference = measure([candidate_path, reference_path])
    return combine(candidate, reference)


def edit_combine(candidate, reference, original):
    """Score an edit: the gain of candidate over the unedited input, relative to the headroom the input leaves."""
    gained = combine(candidate, reference)["score"]
    start = combine(original, reference)["score"]
    return {"score": max(0.0, (gained - start) / (1 - start)), "raw": gained, "input": start}


def score_edit(candidate_path, reference_path, original_path):
    candidate, reference, original = measure([candidate_path, reference_path, original_path])
    return edit_combine(candidate, reference, original)


def _stairs(candidate, reference):
    """The share of stairs, matched by position within STAIR_MATCH and by the level they start at, weighted by how near their rise is."""
    ref, cand = reference["stairs"], candidate["stairs"]
    pairs = _nearest(
        (_distance(r["c"], c["c"]), i, j) for i, r in enumerate(ref) for j, c in enumerate(cand)
        if _distance(r["c"], c["c"]) <= STAIR_MATCH and abs(r["z"] - c["z"]) <= LEVEL_MATCH
    )
    return _share(sum(_ratio(cand[j]["rise"], ref[i]["rise"]) for i, j in pairs.items()), len(ref), len(cand))


def combine_storeys(candidate, reference):
    """Score a building: each reference Level is matched to the candidate Level nearest in elevation, within LEVEL_MATCH, and scored as a flat.
    `storeys` is their mean over the larger count of Levels, `stairs` the share of reference stairs matched, and `score` their product.
    """
    if not candidate["opens"]:
        return {"score": 0.0, "storeys": 0.0, "stairs": 0.0}
    levels = candidate["storeys"] or [{"elevation": 0.0, **candidate}]
    pairs = _nearest(
        (abs(r["elevation"] - c["elevation"]), i, j) for i, r in enumerate(reference["storeys"]) for j, c in enumerate(levels)
        if abs(r["elevation"] - c["elevation"]) <= LEVEL_MATCH
    )
    total = sum(combine(levels[j], reference["storeys"][i])["score"] for i, j in pairs.items())
    storeys = _share(total, len(reference["storeys"]), len(levels))
    stairs = _stairs(candidate, reference)
    return {"score": storeys * stairs, "storeys": storeys, "stairs": stairs}


def score_storeys(candidate_path, reference_path):
    candidate, reference = measure([candidate_path, reference_path])
    return combine_storeys(candidate, reference)
