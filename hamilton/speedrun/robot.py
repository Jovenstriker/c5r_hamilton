"""Build Hamilton programs from pipetting steps instead of firmware fields.

The Robot writes the firmware for you: units, channel lists and masks, flows,
and heights that pass every grader rule. It tracks the liquid in every well
and tip. When the channels cannot reach their wells in one command, it splits
the step into several commands.

    from speedrun import protocol
    from speedrun.robot import Robot

    loaded = protocol.load("protocols/serial-dilution.json")
    robot = Robot(loaded["deck"])
    robot.pick_up(range(8), ul=50)
    robot.aspirate([f"dilution:{row}1" for row in "ABCDEFGH"], 50)
    robot.dispense([f"dilution:{row}2" for row in "ABCDEFGH"], 50, mix=(40, 3))
    robot.eject()
    print(robot.program())

Channels are 0 to 7, back to front. Volumes are liquid microliters. Each
pipetting method takes a list of wells, one per channel, and the channels to
use (default: 0, 1, 2, ... in order). `ul` is one number for all channels
or a list with one number per well.
"""

from __future__ import annotations

import math

from . import protocol

TRAVERSE = 2450
PITCH = 90
FLOOR_MM = 1.1
ASPIRATE_MM = 2.0
BELOW_SURFACE_MM = 0.6
JET_MM = 1.5
FLOWS = {50: (1000, 2000), 1000: (2500, 4000)}


class Robot:
    def __init__(self, deck):
        """Start with no tips, every plate where the deck puts it."""
        self.deck = deck
        self.wires = []
        self.wells = protocol.wells(deck)
        self.volume = {ref: well["volume_microliters"] for ref, well in self.wells.items()}
        self.origin = {name: plate["origin_mm"] for name, plate in protocol.plates(deck).items()}
        self.nest = {
            name: protocol.nest_at(deck, plate, plate["origin_mm"]) for name, plate in protocol.plates(deck).items()
        }
        self.racks = deck["speedrun"]["tips"]
        self.capacity = {rack["name"]: rack["tip_spec"]["capacity_microliters"] for rack in deck["deck"]["tip_racks"]}
        self.free = {name: [(column, row) for column in range(12) for row in range(8)] for name in self.racks}
        self.defined = set()
        self.tip = {}
        self.held = {}
        self.inside = {}
        self.source = {}

    def pick_up(self, channels=range(8), ul=50):
        """Pick up one tip per channel, each from the smallest rack whose tips hold `ul`.

        Takes each rack's next unused spots, column by column, back row first.
        """
        channels = list(channels)
        uls = ul if isinstance(ul, (list, tuple)) else [ul] * len(channels)
        targets = {}
        for channel, volume in sorted(zip(channels, uls)):
            name = self.rack_for(volume)
            rack = self.racks[name]
            column, row = self.free[name].pop(0)
            x, y = rack["a1_mm"][0] + column * rack["pitch_mm"], rack["a1_mm"][1] - row * rack["pitch_mm"]
            targets.setdefault(name, []).append((channel, tenths(x), tenths(y)))
            self.tip[channel], self.held[channel], self.inside[channel] = name, 0, 0.0
            self.source.pop(channel, None)
        for name, spots in targets.items():
            rack = self.racks[name]
            if rack["define"] not in self.defined:
                self.send("C0TT", rack["define"][4:])
                self.defined.add(rack["define"])
            top, bottom = tenths(rack["top_z_mm"]), tenths(rack["top_z_mm"] - rack["stroke_mm"])
            for group in split(spots):
                xs, ys, mask = lists(group, 1, 2)
                self.send(
                    "C0TP", f"xp{xs}yp{ys}tm{mask}tt{rack['define'][6:8]}tp{top:04d}tz{bottom:04d}th{TRAVERSE}td1"
                )

    def aspirate(self, wells, ul, channels=None, doses=1):
        """Aspirate `ul` from each well, 2 mm above its bottom or lower if the liquid is shallow.

        doses: aspirate enough for that many dispenses of `ul` each. Put what
        is left back with empty().
        """
        targets = []
        for channel, ref, volume in self.plan(wells, ul, channels):
            plunger = doses * self.plunger(channel, volume)
            targets.append((channel, *self.xy(ref), ref, plunger, self.liquid(channel, plunger)))
        for group in split(targets):
            zs = []
            for channel, _, _, ref, plunger, liquid in group:
                self.volume[ref] -= liquid
                depth = self.height(ref) - BELOW_SURFACE_MM
                if depth < FLOOR_MM:
                    raise ValueError(f"{ref} keeps too little liquid to aspirate {liquid:.1f} uL below its surface")
                zs.append(math.floor((self.bottom(ref) + min(ASPIRATE_MM, depth)) * 10))
                self.held[channel] += plunger
                self.inside[channel] += liquid
                self.source[channel] = ref
            volumes = [plunger for *_, plunger, _ in group]
            self.send("C0AS", self.aspirate_fields(group, zs, volumes))

    def dispense(self, wells, ul, channels=None, mix=None, jet=False):
        """Dispense `ul` into each well.

        By default the tip touches the liquid 1.1 mm above the well bottom.
        jet=True dispenses 1.5 mm above the liquid surface instead, so the tip
        stays dry and may go on to dispense into other wells.
        mix=(ul, cycles) mixes each well right after dispensing (not with jet).
        """
        if jet and mix:
            raise ValueError("a jet dispense cannot mix: the tip must be in the liquid")
        targets = [(channel, *self.xy(ref), ref, volume) for channel, ref, volume in self.plan(wells, ul, channels)]
        for group in split(targets):
            zs, volumes = [], []
            for channel, _, _, ref, volume in group:
                plunger = self.plunger(channel, volume)
                if plunger > self.held[channel] + 1:
                    raise ValueError(f"channel {channel} holds too little to dispense {volume} uL")
                self.held[channel] -= plunger
                self.inside[channel] -= volume
                self.volume[ref] += volume
                surface = self.bottom(ref) + self.height(ref)
                zs.append(math.ceil((surface + JET_MM) * 10) if jet else self.floor(ref, mix and mix[0]))
                volumes.append(plunger)
            self.send("C0DS", self.dispense_fields(group, zs, volumes, 2, mix))

    def mix(self, wells, ul, cycles, channels=None):
        """Mix each well: draw and push `ul` `cycles` times, 1.1 mm above the bottom."""
        targets = [(channel, *self.xy(ref), ref) for channel, ref, _ in self.plan(wells, ul, channels)]
        for group in split(targets):
            zs = [self.floor(ref, ul) for _, _, _, ref in group]
            self.send("C0AS", self.aspirate_fields(group, zs, [0] * len(group), (ul, cycles)))

    def empty(self, channels=None):
        """Put what is left in each tip back into the well it came from."""
        channels = [c for c in (self.tip if channels is None else channels) if c in self.source]
        targets = [(channel, *self.xy(self.source[channel]), self.source[channel]) for channel in channels]
        for group in split(targets):
            for channel, _, _, ref in group:
                self.volume[ref] += self.inside[channel]
                self.held[channel], self.inside[channel] = 0, 0.0
            zs = [self.floor(ref) for _, _, _, ref in group]
            self.send("C0DS", self.dispense_fields(group, zs, [0] * len(group), 4))

    def eject(self, channels=None):
        """Drop the tips of these channels (default: every tip) into the waste."""
        channels = sorted(self.tip if channels is None else channels)
        waste = self.deck["speedrun"]["waste"]
        ys = " ".join(f"{tenths(y):04d}" for y in waste["y_mm"])
        mask = " ".join("1" if channel in channels else "0" for channel in range(8))
        self.send(
            "C0TR",
            f"xp{tenths(waste['x_mm']):05d}&yp{ys}tp{tenths(waste['top_z_mm']):04d}"
            f"tz{tenths(waste['bottom_z_mm']):04d}th{TRAVERSE}te{TRAVERSE}tm{mask}ti0",
        )
        for channel in channels:
            for state in (self.tip, self.held, self.inside, self.source):
                state.pop(channel, None)

    def move(self, plate, nest):
        """Carry a plate to a nest with the iSWAP, using the deck's templates."""
        templates = {t["nest"]: t for t in self.deck["speedrun"]["iswap_templates"] if t["plate"] == plate}
        self.send("C0PP", templates[self.nest[plate]]["pick"][4:])
        self.send("C0PR", templates[nest]["place"][4:])
        self.send("C0PG", "th2840")
        self.nest[plate], self.origin[plate] = nest, templates[nest]["origin_mm"]

    def program(self):
        """The program so far, one command per line."""
        return "".join(wire + "\n" for wire in self.wires)

    def send(self, opcode, fields):
        self.wires.append(f"{opcode}id{len(self.wires) + 1:04d}{fields}")

    def plan(self, wells, ul, channels):
        channels = list(range(len(wells)) if channels is None else channels)
        uls = ul if isinstance(ul, (list, tuple)) else [ul] * len(wells)
        if not len(wells) == len(channels) == len(uls) or len(set(channels)) < len(channels):
            raise ValueError("give one well, one volume and one distinct channel per tip")
        missing = [channel for channel in channels if channel not in self.tip]
        if missing:
            raise ValueError(f"channels {missing} have no tip")
        return list(zip(channels, wells, uls))

    def rack_for(self, ul):
        fitting = [name for name, rack in self.racks.items() if rack["volume_ul"] >= ul and self.free[name]]
        if not fitting:
            raise ValueError(f"no tips left that hold {ul} uL")
        return min(fitting, key=lambda name: self.racks[name]["volume_ul"])

    def xy(self, ref):
        geometry = protocol.well_geometry(self.deck, ref, self.origin[ref.split(":")[0]])
        return tenths(geometry["x"]), tenths(geometry["y"])

    def bottom(self, ref):
        return protocol.well_geometry(self.deck, ref, self.origin[ref.split(":")[0]])["bottom"]

    def height(self, ref, less=0.0):
        return protocol.liquid_height(self.wells[ref], self.volume[ref] - less)

    def floor(self, ref, mix_ul=None):
        z = math.floor((self.bottom(ref) + FLOOR_MM) * 10)
        if mix_ul and self.height(ref, mix_ul) - BELOW_SURFACE_MM < FLOOR_MM:
            raise ValueError(f"{ref} holds too little liquid to mix {mix_ul} uL in it")
        return z

    def size(self, channel):
        return self.racks[self.tip[channel]]["volume_ul"]

    def curve(self, channel):
        capacity = self.capacity[self.tip[channel]]
        points = next(
            c["points"]
            for c in self.deck["deck"]["correction_curves"]
            if c["tip_capacity_microliters"] == capacity and not c.get("conditions")
        )
        return [(points[i - 1], points[i]) for i in range(1, len(points))]

    def plunger(self, channel, ul):
        for (a, b), (c, d) in self.curve(channel):
            if a <= ul <= c:
                return round(10 * (b + (d - b) * (ul - a) / (c - a)))
        raise ValueError(f"{ul} uL is outside the correction curve of a {self.size(channel)} uL tip")

    def liquid(self, channel, plunger):
        for (a, b), (c, d) in self.curve(channel):
            if 10 * b <= plunger <= 10 * d:
                return a + (c - a) * (plunger / 10 - b) / (d - b)
        raise ValueError(f"{plunger / 10} uL of plunger volume is outside the correction curve")

    def flows(self, group):
        return [FLOWS[50] if self.size(channel) <= 50 else FLOWS[1000] for channel, *_ in group]

    def aspirate_fields(self, group, zs, volumes, mix=None):
        xs, ys, mask = lists(group, 1, 2)
        flows = [aspirate for aspirate, _ in self.flows(group)]
        mv = [self.plunger(channel, mix[0]) if mix else 0 for channel, *_ in group]
        z, lp, av, fl, mv = (fill(group, values, width) for values, width in self.columns(zs, volumes, flows, mv))
        cycles = mix[1] if mix else 0
        return (
            f"at0&tm{mask}xp{xs}yp{ys}th{TRAVERSE}te{TRAVERSE}lp{lp}ch000&zl{z}po0000&zu0000&zr00000&zx{z}"
            f"ip0000&it0&fp0000&av{av}as{fl}ta000&ba0000&oa000&lm0&ll1&lv1&zo005&ld00&de0010&wt00&mv{mv}"
            f"mc{cycles:02d}&mp000&ms{fl}mh0000&gi000&gj0gk0lk0&ik0000&sd0500&se0500&sz0300&io0000&"
        )

    def dispense_fields(self, group, zs, volumes, mode, mix=None):
        xs, ys, mask = lists(group, 1, 2)
        flows = self.flows(group)
        mv = [self.plunger(channel, mix[0]) if mix else 0 for channel, *_ in group]
        z, lp, dv, ds, mv = (
            fill(group, values, width) for values, width in self.columns(zs, volumes, [d for _, d in flows], mv)
        )
        ms = fill(group, [a for a, _ in flows], 4)
        cycles = mix[1] if mix else 0
        return (
            f"dm{mode}&tm{mask}xp{xs}yp{ys}zx{z}lp{lp}zl{z}po0000&ip0000&it0&fp0000&zu0000&zr00000&"
            f"th{TRAVERSE}te{TRAVERSE}dv{dv}ds{ds}ss1000&rv000&ta000&ba0000&lm0&dj00zo005&ll1&lv1&de0010&wt00&"
            f"mv{mv}mc{cycles:02d}&mp000&ms{ms}mh0000&gi000&gj0gk0"
        )

    @staticmethod
    def columns(zs, volumes, flows, mv):
        return [(zs, 4), ([z + 100 for z in zs], 4), (volumes, 5), (flows, 4), (mv, 5)]


def split(targets):
    """Group (channel, x, y, ...) targets into commands the arm can do at once."""
    groups = []
    for target in sorted(targets, key=lambda t: t[0]):
        group = next((g for g in groups if all(fits(other, target) for other in g)), None)
        if group is None:
            groups.append([target])
        else:
            group.append(target)
    return groups


def fits(a, b):
    return a[1] != b[1] or a[2] - b[2] >= PITCH * (b[0] - a[0]) - 1


def fill(group, values, width):
    by_channel = {target[0]: value for target, value in zip(group, values)}
    return " ".join(f"{by_channel.get(channel, values[0]):0{width}d}" for channel in range(8))


def lists(group, x, y):
    mask = " ".join("1" if channel in {t[0] for t in group} else "0" for channel in range(8))
    return fill(group, [t[x] for t in group], 5), fill(group, [t[y] for t in group], 4), mask


def tenths(mm):
    return round(mm * 10)
