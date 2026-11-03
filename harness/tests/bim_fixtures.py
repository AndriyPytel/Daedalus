
import copy
import math

FLOOR = 2.6


def wall(wid, cx, cy, length, ang=0.0, thk=0.2):
    return {"id": wid, "c": [cx, cy], "len": length, "thk": thk, "ang": ang, "h": FLOOR}


def rect(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def flat():
    """Three rooms in an 8 x 5 m outline: A on the left, B above C on the right, a door in the wall between A and B, windows in W2 and W3."""
    return {
        "storey_height": FLOOR,
        "walls": [
            wall("W1", 4.0, 0.0, 8.2),
            wall("W2", 8.0, 2.5, 5.2, math.pi / 2),
            wall("W3", 4.0, 5.0, 8.2),
            wall("W4", 0.0, 2.5, 5.2, math.pi / 2),
            wall("W5", 4.0, 2.5, 5.0, math.pi / 2),
            wall("W6", 6.0, 2.5, 3.8),
        ],
        "doors": [
            {"id": "D1", "c": [4.0, 3.7], "w": 0.9, "h": 2.1, "sill": 0.0, "ang": math.pi / 2, "host": "W5", "kind": "DOOR"},
            {"id": "D2", "c": [8.0, 1.2], "w": 1.0, "h": 2.1, "sill": 0.0, "ang": math.pi / 2, "host": "W2", "kind": "ENTRANCE_DOOR"},
        ],
        "windows": [
            {"id": "N1", "c": [2.0, 5.0], "w": 1.4, "h": 1.2, "sill": 0.9, "ang": 0.0, "host": "W3"},
            {"id": "S1", "c": [6.0, 0.0], "w": 1.2, "h": 1.2, "sill": 0.9, "ang": 0.0, "host": "W1"},
        ],
        "spaces": [
            {"id": "A", "name": "LIVING_ROOM", "poly": rect(0.1, 0.1, 3.9, 4.9)},
            {"id": "B", "name": "BEDROOM", "poly": rect(4.1, 2.6, 7.9, 4.9)},
            {"id": "C", "name": "KITCHEN", "poly": rect(4.1, 0.1, 7.9, 2.4)},
        ],
    }


def without_door(spec, door_id="D1"):
    spec = copy.deepcopy(spec)
    spec["doors"] = [d for d in spec["doors"] if d["id"] != door_id]
    return spec


def with_moved_wall(spec, wall_id="W6", dy=0.9):
    spec = copy.deepcopy(spec)
    next(w for w in spec["walls"] if w["id"] == wall_id)["c"][1] += dy
    return spec


def with_wrong_area(spec, space_id="A"):
    spec = copy.deepcopy(spec)
    next(s for s in spec["spaces"] if s["id"] == space_id)["poly"] = rect(0.1, 0.1, 2.9, 4.9)
    return spec


def with_unhosted_window(spec, window_id="N1"):
    spec = copy.deepcopy(spec)
    next(w for w in spec["windows"] if w["id"] == window_id)["host"] = None
    return spec


def with_wide_window(spec, window_id="N1"):
    spec = copy.deepcopy(spec)
    next(w for w in spec["windows"] if w["id"] == window_id)["w"] += 0.4
    return spec


def with_shifted_walls(spec, dy=0.7):
    spec = copy.deepcopy(spec)
    for item in spec["walls"]:
        item["c"][1] += dy
    return spec
