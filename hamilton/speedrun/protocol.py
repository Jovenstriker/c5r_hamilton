from __future__ import annotations

import json
import math
from pathlib import Path

HAMILTON = Path(__file__).resolve().parent.parent
NEST_TOLERANCE_MM = 0.5
OPS = {"transfer": ("from", "to", "ul"), "mix": ("well", "ul", "cycles"), "move": ("plate", "to")}


def read_json(path, name):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except ValueError:
        raise ValueError(f"{name} is not valid JSON") from None


def load(path):
    protocol = read_json(path, path)
    if not (isinstance(protocol, dict) and all(isinstance(protocol.get(key), str) for key in ("id", "deck"))):
        raise ValueError(f"{path} is not a protocol: it needs an id and a deck")
    deck_path = HAMILTON / protocol["deck"]
    if not deck_path.is_file():
        raise ValueError(f"deck {protocol['deck']} not found")
    deck = read_json(deck_path, protocol["deck"])
    if not (isinstance(deck, dict) and isinstance(deck.get("deck"), dict) and "speedrun" in deck):
        raise ValueError(f"deck {protocol['deck']} is not a speedrun deck")
    loaded = {**protocol, "deck": deck, "deck_path": str(deck_path)}
    check(loaded)
    return loaded


def check(protocol):
    if not isinstance(protocol.get("steps"), list) or not protocol["steps"]:
        raise ValueError("the protocol needs a list of steps")
    deck = protocol["deck"]
    names = {"well": wells(deck), "plate": plates(deck), "nest": {nest["name"] for nest in deck["deck"]["nests"]}}
    for number, step in enumerate(protocol["steps"], 1):
        problem = step_problem(step, names)
        if problem:
            raise ValueError(f"step {number}: {problem}")


def step_problem(step, names):
    op = step.get("op") if isinstance(step, dict) else None
    if not isinstance(op, str) or op not in OPS:
        return f"unknown op {op!r}"
    missing = [key for key in OPS[op] if key not in step]
    if missing:
        return f"{op} needs {' and '.join(missing)}"
    kinds = {"plate": "plate", "to": "nest"} if op == "move" else {"from": "well", "to": "well", "well": "well"}
    for key, kind in kinds.items():
        if key in step and not (isinstance(step[key], str) and step[key] in names[kind]):
            return f"no {kind} {step[key]!r} on the deck"
    if op != "move" and not (isinstance(step["ul"], (int, float)) and step["ul"] > 0):
        return "ul must be a positive number"
    if op == "mix" and not (isinstance(step["cycles"], int) and step["cycles"] > 0):
        return "cycles must be a positive whole number"
    return None


def plates(deck):
    return {plate["name"]: plate for plate in deck["deck"]["plates"]}


def seated_origin(deck, plate, nest):
    module = next(
        (module["name"] for module in deck["deck"].get("modules", []) if module.get("nest") == nest["name"]), None
    )
    overlap = plate.get("seating_overlaps_mm", {}).get(module or nest.get("carrier"), 0.0)
    x, y, z = nest["origin_mm"]
    return [x, y, z - overlap]


def nest_at(deck, plate, origin):
    for nest in deck["deck"]["nests"]:
        if math.dist(seated_origin(deck, plate, nest), origin) <= NEST_TOLERANCE_MM:
            return nest["name"]
    return None


def wells(deck):
    return {f"{plate['name']}:{well['name']}": well for plate in deck["deck"]["plates"] for well in plate["wells"]}


def well_geometry(deck, ref, origin=None):
    plate_name, name = ref.split(":")
    plate = plates(deck)[plate_name]
    well = next(well for well in plate["wells"] if well["name"] == name)
    x, y, z = origin or plate["origin_mm"]
    yaw = math.radians(plate["yaw_degrees"])
    cx, cy = well["center_mm"]
    return {
        "x": x + cx * math.cos(yaw) - cy * math.sin(yaw),
        "y": y + cx * math.sin(yaw) + cy * math.cos(yaw),
        "bottom": z + well["bottom_z_mm"],
        "area": well["area_mm2"],
    }


def liquid_height(well, volume):
    volume = min(volume, well["capacity_microliters"])
    profile = well.get("container_profile")
    if not profile:
        return volume / well["area_mm2"]
    height = 0.0
    for segment in profile["segments"]:
        a, b, c = segment["coefficients"]
        top = segment["height_mm"]
        full = top * (a + top * (b + top * c))
        if volume < full:
            return height + segment_height(a, b, c, top, volume)
        volume -= full
        height += top
    return height


def segment_height(a, b, c, top, volume):
    low, high = 0.0, top
    for _ in range(56):
        middle = (low + high) / 2
        if middle * (a + middle * (b + middle * c)) < volume:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def touches(step):
    if step["op"] == "move":
        return set(), {step["plate"]}
    refs = {step["from"], step["to"]} if step["op"] == "transfer" else {step["well"]}
    return refs, {ref.split(":")[0] for ref in refs}


def depends(a, b):
    wells_a, plates_a = touches(a)
    wells_b, plates_b = touches(b)
    moved = {step["plate"] for step in (a, b) if step["op"] == "move"}
    return bool(wells_a & wells_b or moved & plates_a & plates_b)


def predecessors(steps):
    return [[j for j in range(i) if depends(steps[j], steps[i])] for i in range(len(steps))]


def expect(protocol):
    deck = protocol["deck"]
    volume = {ref: well["volume_microliters"] for ref, well in wells(deck).items()}
    components = {ref: dict(well.get("components_microliters", {})) for ref, well in wells(deck).items()}
    added = dict.fromkeys(volume, 0.0)
    removed = dict.fromkeys(volume, 0.0)
    route = {name: [nest_at(deck, plate, plate["origin_mm"])] for name, plate in plates(deck).items()}
    moves, mixes = [], []
    for index, step in enumerate(protocol["steps"]):
        if step["op"] == "transfer":
            source, target, ul = step["from"], step["to"], step["ul"]
            for name, amount in components[source].items():
                moved = amount * ul / volume[source]
                components[source][name] = amount - moved
                components[target][name] = components[target].get(name, 0.0) + moved
            volume[source] -= ul
            volume[target] += ul
            removed[source] += ul
            added[target] += ul
        elif step["op"] == "mix":
            ref = step["well"]
            mixes.append(
                {
                    "step": index,
                    "well": ref,
                    "ul": step["ul"],
                    "cycles": step["cycles"],
                    "added": added[ref],
                    "removed": removed[ref],
                }
            )
        else:
            plate = step["plate"]
            route[plate].append(step["to"])
            moves.append(
                {
                    "step": index,
                    "plate": plate,
                    "to": step["to"],
                    "volumes": {ref: (added[ref], removed[ref]) for ref in volume if ref.startswith(plate + ":")},
                }
            )
    return {"components": components, "route": route, "moves": moves, "mixes": mixes}
