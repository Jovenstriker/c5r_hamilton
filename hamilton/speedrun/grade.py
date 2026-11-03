from __future__ import annotations

import math
import re

from . import protocol as model
from . import twin

ALLOWED = {
    "C0TT",
    "C0TP",
    "C0TR",
    "C0AS",
    "C0DS",
    "C0PP",
    "C0PR",
    "C0PG",
    "C0RF",
    "C0RX",
    "C0RY",
    "C0RZ",
    "C0RT",
    "C0QP",
}
TRAVERSE_MIN = 2450
FLOW_MAX = {50: {"as": 1000, "ms": 1000, "ds": 2000}, 1000: {"as": 2500, "ms": 2500, "ds": 4000}}
MIX_CYCLES_MAX = 20
MIX_WORK_MAX = 2000
PLUNGER_SLACK = 1
IMMERSION_MM = 0.5
JET_MM = 1.0
FLOOR_MM = 1.0
CENTER_MM = 0.3
RACK_HEIGHT_MM = 0.05
WASTE_MM = 0.5
PARKED = {"arm_rotation": (91.34, 1.5), "arm_wrist": (-135.0, 0.5), "arm_y": (626.65, 1.5)}
LATENCY_S = 0.8
MAX_COMMANDS = 2000
MAX_LINE = 2048
BLANK = " \t\n\r\x0b\x0c"
PRINTABLE = re.compile(r"[ -~]*")
WIRE = re.compile(r"([A-Z0-9]{4})id(\d{4})((?:[a-z]{2}\d+(?: \d+)*&?)*)")
FIELD = re.compile(r"([a-z]{2})(\d+(?: \d+)*&?)")
PINNED = dict(
    FIELD.findall("td1dj00ch000ll1lv1ld00de0010ss1000lk0ik0000sd0500se0500sz0300io0000gi000gj0gk0il00000in0000")
)
PINNED |= {"ip": "0000", "it": "0"}


def values(raw):
    return [int(value) for value in raw.rstrip("&").split(" ")]


def numbers(raw):
    listed = values(raw)
    return listed + listed[-1:] * (8 - len(listed))


def parse(wire):
    match = WIRE.fullmatch(wire)
    if not match:
        return None, None, {}
    return match[1], match[2], dict(FIELD.findall(match[3]))


def answered(line):
    return not (line["refused"] or line["error"]) and line["reply"][10:].startswith("er00")


def channels(fields):
    return [channel for channel, on in enumerate(numbers(fields.get("tm", "0"))) if on]


def close(actual, expected):
    return abs(actual - expected) <= 0.2 + 0.01 * abs(expected)


def near(fields, tag, mm, tolerance):
    return tag in fields and all(abs(value / 10 - mm) <= tolerance + 1e-6 for value in values(fields[tag]))


def table_number(fields):
    return tuple(values(fields["tt"])) if "tt" in fields else None


def mm(fields, tag, channel):
    return numbers(fields.get(tag, "0"))[channel] / 10


def templates(deck):
    wires = [entry[key] for entry in deck["speedrun"]["iswap_templates"] for key in ("pick", "place")]
    return [(wire[:4], {tag: raw for tag, raw in FIELD.findall(wire[4:]) if tag != "id"}) for wire in wires]


def mix_top(opcode, fields, channel):
    start, floor = mm(fields, "zl", channel), mm(fields, "zx", channel)
    if opcode == "C0DS":
        start += following(
            mm(fields, "fp", channel), mm(fields, "zu", channel), mm(fields, "zr", channel) / 1000, start - floor
        )
    return max(start - mm(fields, "mp", channel), floor)


def following(distance, height, ratio, start, direction=1):
    if not (distance and height) or ratio >= 1:
        return distance
    slope = (1 - ratio) / height
    section = height * (1 + ratio + ratio * ratio) / 3
    volume = section + start - height if start >= height else ((ratio + slope * start) ** 3 - ratio**3) / (3 * slope)
    target = max(volume + direction * distance, 0.0)
    end = (
        height + target - section if target >= section else ((ratio**3 + 3 * slope * target) ** (1 / 3) - ratio) / slope
    )
    return abs(end - start)


def mix_cycles(wire):
    opcode, _, fields = parse(wire)
    if opcode not in ("C0AS", "C0DS") or "mc" not in fields:
        return [0], 0
    cycles = numbers(fields["mc"])
    return cycles, sum(count for count, on in zip(cycles, numbers(fields.get("tm", "0"))) if on)


class Run:
    def __init__(self, protocol):
        self.deck = protocol["deck"]
        self.wells = model.wells(self.deck)
        self.offsets = {ref: model.well_geometry(self.deck, ref, (0.0, 0.0, 0.0)) for ref in self.wells}
        self.volume = {ref: well["volume_microliters"] for ref, well in self.wells.items()}
        self.added = dict.fromkeys(self.volume, 0.0)
        self.removed = dict.fromkeys(self.volume, 0.0)
        self.plates = model.plates(self.deck)
        self.origin = {name: plate["origin_mm"] for name, plate in self.plates.items()}
        self.route = {
            name: [model.nest_at(self.deck, plate, plate["origin_mm"])] for name, plate in self.plates.items()
        }
        self.defines = {
            name: dict(FIELD.findall(rack["define"][4:])) for name, rack in self.deck["speedrun"]["tips"].items()
        }
        self.empty = {
            (rack["name"], spot) for rack in self.deck["deck"]["tip_racks"] for spot in rack.get("missing", [])
        }
        self.transfers = {(step["from"], step["to"]) for step in protocol["steps"] if step["op"] == "transfer"}
        self.templates = templates(self.deck)
        self.moves, self.mixes, self.failures = [], [], []
        self.tips, self.tables, self.ids, self.spots = {}, {}, {}, set()
        self.previous = None

    def fail(self, line, text):
        self.failures.append(f"line {line}: {text}" if line else text)

    def command(self, line):
        number, events = line["line"], line["events"]
        opcode, ident, fields = parse(line["wire"])
        ok = answered(line)
        if not ok:
            self.fail(number, f"the robot answered {line['reply'] if line['refused'] else line['reply'][10:]}")
        if opcode is None:
            return self.fail(number, "is not a firmware command (opcode, id and fields only)")
        self.syntax(number, opcode, ident, fields)
        for event in events:
            if event["kind"] == "mix":
                self.mix(number, opcode, fields, event)
            else:
                self.event(number, fields, event)
        if opcode == "C0AS":
            self.immersion(number, fields, events)
        if opcode in ("C0AS", "C0DS") and ok:
            self.plunger(number, opcode, fields)
        if opcode == "C0TT" and ok:
            self.tables[table_number(fields)] = fields
        if opcode == "C0TP":
            self.pickups(number, fields, events)
        if opcode == "C0TR" and ok:
            self.ejects(number, fields, events)
        for rest in line["rested"]:
            self.rest(number, rest)

    def syntax(self, number, opcode, ident, fields):
        if opcode not in ALLOWED:
            self.fail(number, f"{opcode} is not allowed")
        if ident in self.ids:
            self.fail(number, f"repeats id {ident} from line {self.ids[ident]}")
        self.ids[ident] = number
        expected = {"C0PP": "C0PR", "C0PR": "C0PG"}.get(self.previous)
        if expected and opcode != expected:
            self.fail(number, f"{self.previous} must be followed at once by {expected}")
        self.previous = opcode
        if opcode in ("C0PP", "C0PR") and (opcode, fields) not in self.templates:
            self.fail(number, f"{opcode} does not match a published iSWAP template")
        if opcode == "C0TT" and fields not in self.defines.values():
            self.fail(number, "C0TT must equal the define of a tip rack in the deck's speedrun.tips")
        for tag in ("th", "te"):
            if tag in fields and min(numbers(fields[tag])) < TRAVERSE_MIN:
                self.fail(number, f"traverse height {tag} is below {TRAVERSE_MIN}")
        for tag, raw in PINNED.items():
            if tag in fields and set(values(fields[tag])) != {int(raw)}:
                self.fail(number, f"{tag} must be {tag}{raw}: the twin does not model it")
        if opcode in ("C0AS", "C0DS"):
            self.pipetting(number, opcode, fields)

    def pipetting(self, number, opcode, fields):
        if set(values(fields.get("lm", "0"))) != {0}:
            self.fail(number, "lm must be 0 on every channel: liquid level detection is not allowed")
        if not set(values(fields.get("at", "0"))) <= {0, 1}:
            self.fail(number, "at must be 0 or 1")
        active = channels(fields)
        for tag in ("as", "ds", "ms"):
            for channel, flow in enumerate(numbers(fields[tag]) if tag in fields else []):
                size = self.tips.get(channel, {}).get("size", 1000) if channel in active else 1000
                if flow > FLOW_MAX[size][tag]:
                    self.fail(
                        number, f"channel {channel + 1}: flow {tag} is above {FLOW_MAX[size][tag]} for a {size} uL tip"
                    )
        for channel in active:
            x, y = mm(fields, "xp", channel), mm(fields, "yp", channel)
            well = self.center(x, y)
            if well is None:
                self.fail(
                    number,
                    f"channel {channel + 1} at x {x:.1f} y {y:.1f} is not within {CENTER_MM} mm of a well center",
                )
                continue
            ref, bottom = well
            zx, zl = mm(fields, "zx", channel), mm(fields, "zl", channel)
            if mm(fields, "lp", channel) < max(zx, zl):
                self.fail(number, f"channel {channel + 1}: lp must be at or above zl and zx")
            if opcode == "C0AS":
                zl -= following(
                    mm(fields, "fp", channel), mm(fields, "zu", channel), mm(fields, "zr", channel) / 1000, zl - zx, -1
                )
            low = min(zx, zl)
            if low < bottom + FLOOR_MM - 1e-6:
                self.fail(
                    number,
                    f"channel {channel + 1} goes down to z {low:.1f} mm in {ref}: zx, zl and zl - fp must be at least "
                    f"{FLOOR_MM} mm above its bottom {bottom:.2f} mm",
                )

    def center(self, x, y):
        for ref, offset in self.offsets.items():
            ox, oy, oz = self.origin[ref.split(":")[0]]
            if math.hypot(ox + offset["x"] - x, oy + offset["y"] - y) <= CENTER_MM + 1e-6:
                return ref, oz + offset["bottom"]
        return None

    def tip(self, channel):
        return self.tips.setdefault(channel, {"touched": [], "plunger": 0})

    def well(self, event):
        ref = f"{event['plate']}:{event['well']}"
        well = self.wells[ref]
        return ref, well, self.origin[event["plate"]][2] + well["bottom_z_mm"]

    def event(self, number, fields, event):
        kind, channel = event["kind"], event.get("channel")
        ref = f"{event.get('plate')}:{event.get('well')}"
        if kind == "collision":
            self.fail(number, f"collision: {event['part']} hit {event['obstacle']}")
        elif kind == "tip_pickup":
            spot = (event["rack"], event["spot"])
            if spot in self.spots:
                self.fail(number, f"picks a used tip from {event['rack']} {event['spot']}")
            self.spots.add(spot)
            self.empty.add(spot)
            self.tips[channel] = {
                "touched": [],
                "plunger": 0,
                "size": self.deck["speedrun"]["tips"][event["rack"]]["volume_ul"],
            }
        elif kind == "tip_return":
            spot = (event["rack"], event["spot"])
            if spot not in self.empty:
                self.fail(
                    number,
                    f"channel {channel + 1} returns its tip to {event['rack']} {event['spot']}, which holds a tip",
                )
            self.empty.discard(spot)
            self.spots.add(spot)
        elif kind == "aspirate":
            tip = self.tip(channel)
            others = [touched for touched in tip["touched"] if touched != ref]
            if others:
                self.fail(number, f"channel {channel + 1} aspirates {ref} with a tip that touched {others[-1]}")
            tip["touched"] = [ref]
            self.volume[ref] -= event["microliters"]
            self.removed[ref] += event["microliters"]
        elif kind == "dispense":
            self.volume[ref] += event["microliters"]
            if self.tip(channel)["touched"][:1] == [ref]:
                self.removed[ref] -= event["microliters"]
            else:
                self.added[ref] += event["microliters"]
            self.contact(number, channel, ref, self.wet(fields, event), "dispenses into")

    def wet(self, fields, event):
        ref, well, bottom = self.well(event)
        surface = bottom + model.liquid_height(well, self.volume[ref])
        return event["in_liquid"] or mm(fields, "zl", event["channel"]) < surface + JET_MM - 1e-6

    def mix(self, number, opcode, fields, event):
        ref, well, bottom = self.well(event)
        self.mixes.append(
            {
                "well": ref,
                "ul": event["microliters"],
                "cycles": event["cycles"],
                "in_liquid": event["in_liquid"],
                "top": mix_top(opcode, fields, event["channel"]),
                "surface": bottom + model.liquid_height(well, self.volume[ref] - event["microliters"]),
                "added": self.added[ref],
                "removed": self.removed[ref],
            }
        )
        self.contact(number, event["channel"], ref, event["in_liquid"], "mixes")

    def contact(self, number, channel, ref, in_liquid, action):
        touched = self.tip(channel)["touched"]
        if touched[-1:] == [ref] or (len(touched) == 1 and not in_liquid):
            return
        if touched and (len(touched) == 2 or (touched[0], ref) not in self.transfers):
            return self.fail(number, f"channel {channel + 1} {action} {ref} with a tip that touched {touched[-1]}")
        if in_liquid:
            touched.append(ref)

    def plunger(self, number, opcode, fields):
        volumes = numbers(fields["av" if opcode == "C0AS" else "dv"])
        modes = numbers(fields.get("dm", "0"))
        for channel in channels(fields):
            tip, volume = self.tip(channel), volumes[channel]
            if opcode == "C0AS":
                tip["plunger"] += volume
                continue
            if volume > tip["plunger"] + PLUNGER_SLACK:
                self.fail(
                    number,
                    f"channel {channel + 1} dispenses {volume / 10:.1f} uL of plunger volume but its tip holds "
                    f"{tip['plunger'] / 10:.1f} (av in minus dv out since pickup)",
                )
            tip["plunger"] = 0 if modes[channel] == 4 else tip["plunger"] - volume

    def immersion(self, number, fields, events):
        for event in events:
            if event["kind"] != "aspirate":
                continue
            ref, well, bottom = self.well(event)
            surface = bottom + model.liquid_height(well, self.volume[ref])
            height = mm(fields, "zl", event["channel"])
            if height > surface - IMMERSION_MM + 1e-6:
                self.fail(
                    number,
                    f"channel {event['channel'] + 1} aspirates {ref} at z {height:.1f} mm, above "
                    f"its liquid surface {surface:.1f} mm minus {IMMERSION_MM} mm",
                )

    def pickups(self, number, fields, events):
        table = self.tables.get(table_number(fields))
        for event in events:
            if event["kind"] == "tip_pickup" and table != self.defines[event["rack"]]:
                self.fail(number, f"tip type tt{fields.get('tt')} does not match the tips in {event['rack']}")
        self.seat(number, fields, {event["rack"] for event in events if event["kind"] == "tip_pickup"})

    def seat(self, number, fields, racks):
        for rack in sorted(racks):
            spec = self.deck["speedrun"]["tips"][rack]
            top, bottom = spec["top_z_mm"], spec["top_z_mm"] - spec["stroke_mm"]
            if not (near(fields, "tp", top, RACK_HEIGHT_MM) and near(fields, "tz", bottom, RACK_HEIGHT_MM)):
                self.fail(
                    number,
                    f"tp/tz must be {rack}'s top_z_mm and top_z_mm - stroke_mm: tp{round(top * 10)} tz{round(bottom * 10)}",
                )

    def ejects(self, number, fields, events):
        returned = {event["channel"]: event["rack"] for event in events if event["kind"] == "tip_return"}
        waste = self.deck["speedrun"]["waste"]
        wasted, racks = False, set(returned.values())
        for channel in channels(fields):
            bare = self.tips.pop(channel, None) is None
            x, y = mm(fields, "xp", channel), mm(fields, "yp", channel)
            spot = self.rack_spot(x, y)
            if channel in returned:
                continue
            if bare and spot in self.empty:
                racks.add(spot[0])
            elif abs(x - waste["x_mm"]) <= WASTE_MM + 1e-6 and abs(y - waste["y_mm"][channel]) <= WASTE_MM + 1e-6:
                wasted = True
            else:
                self.fail(
                    number,
                    f"channel {channel + 1} ejects outside the waste and the free rack spots "
                    f"(its waste position is xp{round(waste['x_mm'] * 10):05d} yp{round(waste['y_mm'][channel] * 10):04d})",
                )
        self.seat(number, fields, racks)
        top, bottom = waste["top_z_mm"], waste["bottom_z_mm"]
        if wasted and not (
            values(fields["ti"]) == [0] and near(fields, "tp", top, WASTE_MM) and near(fields, "tz", bottom, WASTE_MM)
        ):
            self.fail(number, f"ejects into the waste need ti0 and tp{round(top * 10)} tz{round(bottom * 10)} (+-5)")

    def rack_spot(self, x, y):
        for name, rack in self.deck["speedrun"]["tips"].items():
            (ax, ay), pitch = rack["a1_mm"], rack["pitch_mm"]
            column, row = round((x - ax) / pitch), round((ay - y) / pitch)
            if (
                0 <= column < 12
                and 0 <= row < 8
                and math.hypot(ax + column * pitch - x, ay - row * pitch - y) <= CENTER_MM
            ):
                return name, f"{'ABCDEFGH'[row]}{column + 1}"
        return None

    def rest(self, number, rest):
        plate = rest["plate"]
        self.origin[plate] = rest["origin_mm"]
        self.route[plate].append(model.nest_at(self.deck, self.plates[plate], rest["origin_mm"]))
        volumes = {ref: (self.added[ref], self.removed[ref]) for ref in self.wells if ref.startswith(plate + ":")}
        self.moves.append({"plate": plate, "line": number, "volumes": volumes})

    def check_moves(self, plan):
        for plate, route in plan["route"].items():
            if self.route[plate] != route:
                self.fail(
                    None,
                    f"plate {plate} rests at {' -> '.join(map(str, self.route[plate]))}, "
                    f"expected {' -> '.join(map(str, route))}",
                )
                continue
            done = [move for move in self.moves if move["plate"] == plate]
            for move, need in zip(done, [move for move in plan["moves"] if move["plate"] == plate]):
                wrong = [
                    ref
                    for ref, (added, removed) in need["volumes"].items()
                    if not (close(move["volumes"][ref][0], added) and close(move["volumes"][ref][1], removed))
                ]
                if wrong:
                    self.fail(
                        move["line"],
                        f"plate {plate} moves to {need['to']} when {wrong[0]} has not had "
                        f"exactly the steps before step {need['step'] + 1}",
                    )

    def check_mixes(self, plan):
        for need in plan["mixes"]:
            timed = [
                mix
                for mix in self.mixes
                if mix["well"] == need["well"]
                and mix["in_liquid"]
                and mix["cycles"] >= need["cycles"]
                and mix["ul"] >= 0.9 * need["ul"] - 1e-9
                and close(mix["added"], need["added"])
                and close(mix["removed"], need["removed"])
            ]
            if any(mix["top"] <= mix["surface"] - IMMERSION_MM + 1e-6 for mix in timed):
                continue
            text = f"step {need['step'] + 1}: {need['well']} is not mixed in liquid ({need['cycles']} x {need['ul']} uL) at that point"
            if timed:
                text += (
                    f": the mix stroke starts at z {timed[0]['top']:.1f} mm, above the liquid surface after "
                    f"the mix draw {timed[0]['surface']:.1f} mm minus {IMMERSION_MM} mm"
                )
            self.fail(None, text)

    def check_final(self, plan, final):
        for plate in final["deck"]["plates"]:
            nest = model.nest_at(self.deck, self.plates[plate["name"]], plate["origin_mm"])
            if nest != plan["route"][plate["name"]][-1]:
                self.fail(None, f"plate {plate['name']} ends at {nest}, expected {plan['route'][plate['name']][-1]}")
            for well in plate["wells"]:
                ref = f"{plate['name']}:{well['name']}"
                expected, actual = plan["components"][ref], well.get("components_microliters", {})
                for name in sorted(expected.keys() | actual.keys()):
                    if not close(actual.get(name, 0.0), expected.get(name, 0.0)):
                        self.fail(
                            None,
                            f"{ref} holds {actual.get(name, 0.0):.2f} uL {name}, "
                            f"expected {expected.get(name, 0.0):.2f}",
                        )
        if any(final["tips"]):
            self.fail(None, "tips are still fitted at the end")
        if final["grip"]["held"] != "open":
            self.fail(None, "the iSWAP still holds a plate at the end")
        if any(abs(final["pose"][axis] - value) > tolerance for axis, (value, tolerance) in PARKED.items()):
            self.fail(None, "the iSWAP is not parked at the end")


def grade(protocol, trace):
    run = Run(protocol)
    lines = [line for line in trace if "line" in line]
    for line in lines:
        run.command(line)
    plan = model.expect(protocol)
    run.check_moves(plan)
    run.check_mixes(plan)
    if trace and "final" in trace[-1]:
        run.check_final(plan, trace[-1]["final"])
    else:
        run.fail(None, "the twin stopped before the end of the program")
    twin_seconds = sum(line["ms"] for line in lines) / 1000
    return {
        "valid": not run.failures,
        "seconds": None if run.failures else round(twin_seconds + LATENCY_S * len(lines), 3),
        "twin_seconds": round(twin_seconds, 3),
        "commands": len(lines),
        "failures": run.failures,
    }


def lint(program):
    lines, commands, work = [], 0, 0
    for number, line in enumerate(program.split("\n"), 1):
        wire = line.strip(BLANK)
        if not wire or wire.startswith("#"):
            continue
        commands += 1
        if len(wire) > MAX_LINE:
            lines.append(f"line {number}: is {len(wire)} characters long, the limit is {MAX_LINE}")
        elif not PRINTABLE.fullmatch(wire):
            lines.append(f"line {number}: has a character that is not printable ASCII")
        else:
            cycles, active = mix_cycles(wire)
            work += active
            if max(cycles) > MIX_CYCLES_MAX:
                lines.append(f"line {number}: mix cycles mc are above {MIX_CYCLES_MAX}")
    failures = []
    if "\r" in program.replace("\r\n", ""):
        failures.append("the program has a carriage return that does not end a line: use LF or CRLF line endings")
    if not commands:
        failures.append("the program has no commands")
    if commands > MAX_COMMANDS:
        failures.append(f"the program has {commands} commands, the limit is {MAX_COMMANDS}")
    if work > MIX_WORK_MAX:
        failures.append(f"the program has {work} mix cycles over all channels, the limit is {MIX_WORK_MAX}")
    return failures + lines


def score(protocol, program):
    failures = lint(program)
    if not failures:
        try:
            trace = twin.run(protocol["deck_path"], program)
        except RuntimeError as error:
            failures = [str(error)]
        else:
            return grade(protocol, trace), trace
    return {"valid": False, "seconds": None, "twin_seconds": None, "commands": 0, "failures": failures}, []
