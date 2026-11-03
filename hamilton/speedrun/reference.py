"""The reference solver: correct, simple and slow.

Every step runs on its own, on channel 1, with a fresh tip:

- transfer: pick up a tip, aspirate near the bottom of the source well,
  dispense 1.1 mm above the bottom of the target well, eject the tip into the
  waste.
- mix: pick up a tip, mix in the well (an aspirate command with no volume),
  eject the tip.
- move: grip the plate with the deck's iSWAP templates, set it down, park.

The firmware takes positions in 0.1 mm, volumes in 0.1 uL and flows in
0.1 uL/s. Aspirate, dispense and mix volumes are plunger volumes: the deck's
correction curve for the tip turns a liquid volume into a plunger volume.
"""

from __future__ import annotations

import math

TRAVERSE = 2450
FLOW = 1000
FLOOR_MM = 1.1
ASPIRATE_ABOVE_BOTTOM_MM = 2.0
BELOW_SURFACE_MM = 0.6


def solve(protocol, deck):
    robot = Robot(deck)
    for step in protocol["steps"]:
        getattr(robot, step["op"])(step)
    return robot.wires


class Robot:
    def __init__(self, deck):
        self.deck = deck
        self.plates = {plate["name"]: plate for plate in deck["deck"]["plates"]}
        self.origin = {name: plate["origin_mm"] for name, plate in self.plates.items()}
        self.volume = {
            f"{plate['name']}:{well['name']}": well["volume_microliters"]
            for plate in deck["deck"]["plates"]
            for well in plate["wells"]
        }
        self.templates = deck["speedrun"]["iswap_templates"]
        self.nest = {name: self.home(name) for name in self.plates}
        self.racks = deck["speedrun"]["tips"]
        self.capacities = {rack["name"]: rack["tip_spec"]["capacity_microliters"] for rack in deck["deck"]["tip_racks"]}
        self.used = {name: 0 for name in self.racks}
        self.defined = set()
        self.capacity = None
        self.wires = []

    def send(self, opcode, fields):
        self.wires.append(f"{opcode}id{len(self.wires) + 1:04d}{fields}")

    def transfer(self, step):
        self.pick_up(step["ul"])
        self.aspirate(step["from"], step["ul"])
        self.dispense(step["to"], step["ul"])
        self.eject()

    def mix(self, step):
        self.pick_up(step["ul"])
        x, y, bottom, _ = self.well(step["well"])
        z = below(bottom + min(FLOOR_MM, self.depth(step["well"], step["ul"])))
        self.send("C0AS", aspirate_fields(x, y, z, 0, mix=(self.plunger(step["ul"]), step["cycles"])))
        self.eject()

    def move(self, step):
        plate, nest = step["plate"], step["to"]
        target = self.template(plate, nest)
        self.send("C0PP", self.template(plate, self.nest[plate])["pick"][4:])
        self.send("C0PR", target["place"][4:])
        self.send("C0PG", "th2840")
        self.nest[plate], self.origin[plate] = nest, target["origin_mm"]

    def pick_up(self, ul):
        name = self.rack_for(ul)
        rack = self.racks[name]
        column, row = divmod(self.used[name], 8)
        self.used[name] += 1
        if rack["define"] not in self.defined:
            self.send("C0TT", rack["define"][4:])
            self.defined.add(rack["define"])
        x = tenths(rack["a1_mm"][0] + column * rack["pitch_mm"])
        y = tenths(rack["a1_mm"][1] - row * rack["pitch_mm"])
        top, bottom = tenths(rack["top_z_mm"]), tenths(rack["top_z_mm"] - rack["stroke_mm"])
        table = rack["define"][6:8]
        self.send("C0TP", f"xp{x:05d}&yp{y:04d}&tm1 0&tt{table}tp{top:04d}tz{bottom:04d}th{TRAVERSE}td1")
        self.capacity = self.capacities[name]

    def rack_for(self, ul):
        fitting = [name for name, rack in self.racks.items() if rack["volume_ul"] >= ul and self.used[name] < 96]
        if not fitting:
            raise ValueError(f"no tip left for {ul} uL")
        return min(fitting, key=lambda name: self.racks[name]["volume_ul"])

    def aspirate(self, ref, ul):
        x, y, bottom, _ = self.well(ref)
        z = below(bottom + min(ASPIRATE_ABOVE_BOTTOM_MM, self.depth(ref, ul)))
        self.volume[ref] -= ul
        self.send("C0AS", aspirate_fields(x, y, z, self.plunger(ul)))

    def depth(self, ref, ul):
        depth = (self.volume[ref] - ul) / self.well(ref)[3] - BELOW_SURFACE_MM
        if depth < FLOOR_MM:
            raise ValueError(f"{ref} keeps too little liquid to draw {ul} uL below its surface")
        return depth

    def dispense(self, ref, ul):
        x, y, bottom, _ = self.well(ref)
        self.volume[ref] += ul
        self.send("C0DS", dispense_fields(x, y, below(bottom + FLOOR_MM), self.plunger(ul)))

    def eject(self):
        waste = self.deck["speedrun"]["waste"]
        x, y = tenths(waste["x_mm"]), tenths(waste["y_mm"][0])
        top, bottom = tenths(waste["top_z_mm"]), tenths(waste["bottom_z_mm"])
        self.send("C0TR", f"xp{x:05d}&yp{y:04d}&tp{top:04d}tz{bottom:04d}th{TRAVERSE}te{TRAVERSE}tm1 0&ti0")

    def well(self, ref):
        plate_name, name = ref.split(":")
        well = next(well for well in self.plates[plate_name]["wells"] if well["name"] == name)
        x, y, z = self.origin[plate_name]
        return (
            tenths(x + well["center_mm"][0]),
            tenths(y + well["center_mm"][1]),
            z + well["bottom_z_mm"],
            well["area_mm2"],
        )

    def plunger(self, ul):
        curve = next(
            curve["points"]
            for curve in self.deck["deck"]["correction_curves"]
            if curve["tip_capacity_microliters"] == self.capacity and not curve.get("conditions")
        )
        for index in range(1, len(curve)):
            (liquid_a, plunger_a), (liquid_b, plunger_b) = curve[index - 1], curve[index]
            if liquid_a <= ul <= liquid_b:
                return round(10 * (plunger_a + (plunger_b - plunger_a) * (ul - liquid_a) / (liquid_b - liquid_a)))
        raise ValueError(f"{ul} uL is outside the correction curve")

    def home(self, plate):
        for template in self.templates:
            if template["plate"] == plate and math.dist(template["origin_mm"], self.origin[plate]) < 0.01:
                return template["nest"]
        return None

    def template(self, plate, nest):
        return next(t for t in self.templates if t["plate"] == plate and t["nest"] == nest)


def tenths(mm):
    return round(mm * 10)


def below(mm):
    return math.floor(mm * 10)


def aspirate_fields(x, y, z, volume, mix=(0, 0)):
    mix_volume, cycles = mix
    return (
        f"at0&tm1 0&xp{x:05d}&yp{y:04d}&th{TRAVERSE}te{TRAVERSE}lp{z + 100:04d}&ch000&"
        f"zl{z:04d}&po0050&zu0000&zr00000&zx{z:04d}&ip0000&it0&fp0000&av{volume:05d}&as{FLOW:04d}&"
        f"ta000&ba0000&oa000&lm0&ll1&lv1&zo005&ld00&de0010&wt00&mv{mix_volume:05d}&mc{cycles:02d}&mp000&"
        f"ms{FLOW:04d}&mh0000&gi000&gj0gk0lk0&ik0000&sd0500&se0500&sz0300&io0000&"
    )


def dispense_fields(x, y, z, volume):
    return (
        f"dm2&tm1 0&xp{x:05d}&yp{y:04d}&zx{z:04d}&lp{z + 100:04d}&zl{z:04d}&po0050&"
        f"ip0000&it0&fp0000&zu0000&zr00000&th{TRAVERSE}te{TRAVERSE}dv{volume:05d}&ds{FLOW:04d}&"
        f"ss1000&rv000&ta000&ba0000&lm0&dj00zo005&ll1&lv1&de0010&wt00&mv00000&mc00&mp000&"
        f"ms{FLOW:04d}&mh0000&gi000&gj0gk0"
    )
