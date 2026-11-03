"""A faster solver: one tip serves every transfer from its source well.

Each job is one tip's work: optionally mix its source, aspirate once (or in
a few chunks), jet into every destination but the last, and finish with a
dm4 dispense that empties the tip exactly, mixing there if the protocol
mixes that well next. Up to eight jobs run in lockstep per round. Tips go
back into their rack spots instead of the far-away waste.

solve() tries a few variants (tip size, eject target), scores each on the
twin and prints the fastest valid program, falling back to the starter.

    python3 -m speedrun.mine protocols/serial-dilution.json > program.txt
"""

from __future__ import annotations

import math
import random
import sys

from . import grade, protocol, starter
from .robot import BELOW_SURFACE_MM, FLOOR_MM, JET_MM, TRAVERSE, Robot, split, tenths

RIM_MM = 0.5


class Bot(Robot):
    def __init__(self, deck, prefer="small", back=True):
        super().__init__(deck)
        self.prefer, self.back = prefer, back
        self.spot = {}

    # ---- tips -----------------------------------------------------------

    def size_of(self, name):
        return self.racks[name]["volume_ul"]

    def choose_rack(self, need_ul, count):
        racks = sorted(self.racks, key=self.size_of, reverse=self.prefer == "large")
        for name in racks:
            if self.size_of(name) >= need_ul and len(self.free[name]) >= count:
                return name
        for name in racks:
            if self.size_of(name) >= need_ul and self.free[name]:
                return name
        raise ValueError(f"no tips left that hold {need_ul} uL")

    def take(self, channels, need_ul):
        """Pick up one tip per channel, row = channel where a column allows it."""
        channels = sorted(channels)
        name = self.choose_rack(need_ul, len(channels))
        free = set(self.free[name])
        spots = None
        for column in range(12):
            if all((column, c) in free for c in channels):
                spots = [(column, c) for c in channels]
                break
        if spots is None:
            spots = sorted(free)[: len(channels)]
            if len(spots) < len(channels):
                rest = [c for c in channels][len(spots):]
                self.take_spots(name, list(zip(channels, spots)))
                return self.take(rest, need_ul)
        self.take_spots(name, list(zip(channels, spots)))

    def take_spots(self, name, pairs):
        rack = self.racks[name]
        targets = []
        for channel, (column, row) in pairs:
            self.free[name].remove((column, row))
            x, y = rack["a1_mm"][0] + column * rack["pitch_mm"], rack["a1_mm"][1] - row * rack["pitch_mm"]
            targets.append((channel, tenths(x), tenths(y)))
            self.tip[channel], self.held[channel], self.inside[channel] = name, 0, 0.0
            self.spot[channel] = (name, tenths(x), tenths(y))
            self.source.pop(channel, None)
        if rack["define"] not in self.defined:
            self.send("C0TT", rack["define"][4:])
            self.defined.add(rack["define"])
        top, bottom = tenths(rack["top_z_mm"]), tenths(rack["top_z_mm"] - rack["stroke_mm"])
        for group in split(targets):
            xs, ys, mask = lists8(group)
            self.send("C0TP", f"xp{xs}yp{ys}tm{mask}tt{rack['define'][6:8]}tp{top:04d}tz{bottom:04d}th{TRAVERSE}td1")

    def drop(self):
        channels = sorted(self.tip)
        if not self.back:
            self.eject(channels)
            return
        by_rack = {}
        for channel in channels:
            name, x, y = self.spot[channel]
            by_rack.setdefault(name, []).append((channel, x, y))
        for name, targets in by_rack.items():
            rack = self.racks[name]
            top, bottom = tenths(rack["top_z_mm"]), tenths(rack["top_z_mm"] - rack["stroke_mm"])
            for group in split(targets):
                xs, ys, mask = lists8(group)
                self.send("C0TR", f"xp{xs}yp{ys}tp{top:04d}tz{bottom:04d}th{TRAVERSE}te{TRAVERSE}tm{mask}ti1")
        for channel in channels:
            for state in (self.tip, self.held, self.inside, self.source, self.spot):
                state.pop(channel, None)

    # ---- liquid ---------------------------------------------------------

    def cap(self, channel):
        """Most liquid one aspiration may take into this tip."""
        points = [p for (p, _), _ in self.curve(channel)] + [self.curve(channel)[-1][1][0]]
        return min(max(points), self.size(channel))

    def room(self, ref, mix_ul=0.0):
        """Most liquid that can leave ref with the tip still 0.5 mm under the surface."""
        lo, hi = 0.0, self.volume[ref]
        if self.height(ref) - BELOW_SURFACE_MM < FLOOR_MM:
            return 0.0
        for _ in range(50):
            mid = (lo + hi) / 2
            if self.height(ref, mid) - BELOW_SURFACE_MM - 0.15 >= FLOOR_MM:
                lo = mid
            else:
                hi = mid
        return lo

    def asp(self, items, mix=None):
        """items: (channel, ref, liquid ul). One aspiration each, mixing first if asked."""
        targets = []
        for channel, ref, ul in items:
            plunger = self.plunger(channel, ul) if ul > 0 else 0
            targets.append((channel, *self.xy(ref), ref, plunger))
        for group in split(targets):
            zs, volumes = [], []
            for channel, _, _, ref, plunger in group:
                liquid = self.liquid(channel, plunger) if plunger else 0.0
                depth = self.height(ref, liquid) - BELOW_SURFACE_MM
                if mix:
                    depth = min(depth, self.height(ref, mix[0]) - BELOW_SURFACE_MM)
                if depth < FLOOR_MM:
                    raise ValueError(f"{ref} is too shallow")
                self.volume[ref] -= liquid
                zs.append(math.floor((self.bottom(ref) + depth) * 10))
                self.held[channel] += plunger
                self.inside[channel] += liquid
                self.source[channel] = ref
                volumes.append(plunger)
            self.send("C0AS", self.aspirate_fields(group, zs, volumes, mix))

    def disp(self, items, last, jet, mix=None):
        """items: (channel, ref, ul). last: dm4, empty the tip exactly."""
        targets = [(channel, *self.xy(ref), ref, ul) for channel, ref, ul in items]
        for group in split(targets):
            zs, volumes = [], []
            for channel, _, _, ref, ul in group:
                if last:
                    plunger, liquid = self.held[channel], self.inside[channel]
                else:
                    plunger, liquid = self.plunger(channel, ul), ul
                    if plunger > self.held[channel] + 1:
                        raise ValueError("plunger underflow")
                self.held[channel] -= plunger
                self.inside[channel] -= liquid
                self.volume[ref] += liquid
                surface = self.bottom(ref) + self.height(ref)
                zs.append(self.jet_z(ref, surface) if jet else self.mix_z(ref, mix))
                volumes.append(plunger)
            self.send("C0DS", self.dispense_fields(group, zs, volumes, 4 if last else 2, mix))
        for channel, _, _ in items:
            if last:
                self.held[channel], self.inside[channel] = 0, 0.0


    def top(self, ref):
        well = self.wells[ref]
        return self.bottom(ref) + protocol.liquid_height(well, well["capacity_microliters"])

    def jet_z(self, ref, surface):
        low = math.ceil((surface + JET_MM) * 10)
        return max(low, math.floor((self.top(ref) - RIM_MM) * 10))

    def mix_z(self, ref, mix):
        """Highest dispense height that keeps the whole mix stroke in the liquid."""
        low = self.floor(ref, mix and mix[0])
        if not mix:
            return low
        depth = self.height(ref, mix[0]) - BELOW_SURFACE_MM - 0.1
        return max(low, math.floor((self.bottom(ref) + depth) * 10))


def lists8(group):
    xs = " ".join(f"{dict((t[0], t[1]) for t in group).get(c, group[0][1]):05d}" for c in range(8))
    ys = " ".join(f"{dict((t[0], t[2]) for t in group).get(c, group[0][2]):04d}" for c in range(8))
    on = {t[0] for t in group}
    return xs, ys, " ".join("1" if c in on else "0" for c in range(8))


# ---- planning ---------------------------------------------------------------


def build_jobs(steps, before):
    """Group steps into jobs, one tip each. Every outside predecessor of a job
    comes before the job's first step, so jobs form a DAG in first-step order."""
    jobs, owner = [], {}
    for i, step in enumerate(steps):
        if i in owner or step["op"] == "move":
            continue
        job = {"first": i, "steps": [i], "pre_mix": None, "source": None, "out": [], "mix": None}
        members = {i}

        def ok(j):
            return all(p in members or p < i for p in before[j])

        if step["op"] == "mix":
            job["pre_mix"] = (step["ul"], step["cycles"])
            job["source"] = step["well"]
        else:
            job["source"] = step["from"]
            job["out"].append((step["to"], step["ul"]))
        closed = False
        for j in range(i + 1, len(steps)):
            if closed:
                break
            if j in owner:
                continue
            other = steps[j]
            if other["op"] == "transfer" and other["from"] == job["source"] and ok(j):
                if job["out"] and job["out"][-1][0] == job["source"]:
                    break
                job["steps"].append(j)
                members.add(j)
                job["out"].append((other["to"], other["ul"]))
            elif other["op"] == "mix" and job["out"] and other["well"] == job["out"][-1][0] and ok(j):
                job["steps"].append(j)
                members.add(j)
                job["mix"] = (other["ul"], other["cycles"])
                closed = True
            elif job["pre_mix"] and not job["out"] and protocol.depends(other, step):
                break
        for j in job["steps"]:
            owner[j] = len(jobs)
        job["deps"] = {owner_of for j in job["steps"] for p in before[j] if p not in members for owner_of in [p]}
        jobs.append(job)
    for job in jobs:
        job["deps"] = {("step", p) for p in job["deps"]}
    return jobs, owner


def signature(job):
    return (bool(job["pre_mix"]), len(job["out"]), job["mix"])


def chunks(bot, channel, job):
    """Split a job's dispenses into aspirations: [(liquid, [(dest, ul)])]."""
    parts = greedy_chunks(bot, channel, job, list(job["out"]))
    if OPTIONS["small_first"] and len(parts) > 1:
        sizes = [len(outs) for _, outs in parts][::-1]
        out, flipped, start = list(job["out"]), [], 0
        for size in sizes:
            outs = out[start : start + size]
            flipped.append((sum(ul for _, ul in outs), outs))
            start += size
        if all(fits_chunk(bot, channel, outs) for _, outs in flipped):
            return flipped
    return parts


def fits_chunk(bot, channel, outs):
    total = sum(ul for _, ul in outs)
    return total <= bot.cap(channel) + 1e-9 and bot.plunger(channel, total) + 1 >= sum(
        bot.plunger(channel, ul) for _, ul in outs[:-1]
    )


def greedy_chunks(bot, channel, job, out):
    if not out:
        return []
    cap = bot.cap(channel)
    room = bot.room(job["source"])
    result, start = [], 0
    while start < len(out):
        end, total = start, 0.0
        while end < len(out):
            total2 = total + out[end][1]
            if total2 > cap + 1e-9 or total2 > room + 1e-9:
                break
            if bot.plunger(channel, total2) + 1 < sum(bot.plunger(channel, ul) for _, ul in out[start:end]):
                break
            total, end = total2, end + 1
        if end == start:
            raise ValueError("a transfer does not fit one aspiration")
        result.append((total, out[start:end]))
        room -= total
        start = end
    return result


OPTIONS = {"mix_frac": 1.0, "small_first": False}


def scaled(mix):
    """The protocol accepts a mix of 90% of its volume or more."""
    return mix and (round(mix[0] * OPTIONS["mix_frac"], 1), mix[1])


def run_round(bot, picked):
    """picked: [(channel, job)]. Pick up, run every job in lockstep, drop tips."""
    need = max(max([ul for _, ul in job["out"]] + [0]) for _, job in picked)
    need = max(
        [need]
        + [job["mix"][0] for _, job in picked if job["mix"]]
        + [job["pre_mix"][0] for _, job in picked if job["pre_mix"]]
    )
    bot.take([c for c, _ in picked], need)
    programs = {}
    for channel, job in picked:
        ops = []
        parts = chunks(bot, channel, job)
        if not parts:
            ops.append(("asp", job["source"], 0.0, scaled(job["pre_mix"])))
        for k, (total, outs) in enumerate(parts):
            ops.append(("asp", job["source"], total, scaled(job["pre_mix"]) if k == 0 else None))
            for n, (dest, ul) in enumerate(outs):
                last = n == len(outs) - 1
                final = last and k == len(parts) - 1
                touch = final and job["mix"] is not None
                ops.append(("disp", dest, ul, last, not touch, scaled(job["mix"]) if touch else None))
        programs[channel] = ops
    depth = max(len(ops) for ops in programs.values())
    for phase in range(depth):
        groups = {}
        for channel, ops in programs.items():
            if phase < len(ops):
                op = ops[phase]
                key = (op[0], op[3]) if op[0] == "asp" else (op[0], op[3], op[4], op[5])
                groups.setdefault(key, []).append((channel, op))
        for key, members in groups.items():
            if key[0] == "asp":
                bot.asp([(c, op[1], op[2]) for c, op in members], key[1])
            else:
                bot.disp([(c, op[1], op[2]) for c, op in members], key[1], key[2], key[3])
    bot.drop()


def ycoord(bot, ref):
    return bot.xy(ref)[1]


def solve_variant(loaded, prefer, back, width=8):
    steps = loaded["steps"]
    deck = loaded["deck"]
    before = protocol.predecessors(steps)
    jobs, owner = build_jobs(steps, before)
    bot = Bot(deck, prefer, back)
    done_steps, done_jobs = set(), set()
    planned = []
    moves = [i for i, s in enumerate(steps) if s["op"] == "move"]
    while len(done_steps) < len(steps):
        ready_moves = [i for i in moves if i not in done_steps and done_steps.issuperset(before[i])]
        avail = [
            n
            for n, job in enumerate(jobs)
            if n not in done_jobs and all(p in done_steps for _, p in job["deps"])
        ]
        if ready_moves and (not avail or min(ready_moves) < min(jobs[n]["first"] for n in avail)):
            i = ready_moves[0]
            bot.move(steps[i]["plate"], steps[i]["to"])
            done_steps.add(i)
            continue
        if not avail:
            raise ValueError("deadlock")
        if not planned:
            planned = plan_batches(bot, jobs, avail, width)
        chosen = planned.pop(0)
        picked = best_assignment(bot, [jobs[n] for n in chosen])
        run_round(bot, picked)
        for n in chosen:
            done_jobs.add(n)
            done_steps.update(jobs[n]["steps"])
    return bot.program()


STOP_S = 2.3
ASPIRATE_STOP_S = 2.8
DISPENSE_STOP_S = 2.4
COMMAND_S = 0.8


def phases(bot, job):
    """Where the tip goes, phase by phase: the source, then each destination."""
    return [bot.xy(job["source"])] + [bot.xy(dest) for dest, _ in job["out"]]


def stops(targets, stop=STOP_S):
    """Estimated seconds for one phase: per command, plus per X stop."""
    if not targets:
        return 0.0
    groups = split(targets)
    return COMMAND_S * len(groups) + stop * sum(len({t[1] for t in g}) for g in groups)


def round_cost(places, channels):
    total = 0.0
    for phase in range(max(len(p) for p in places)):
        stop = ASPIRATE_STOP_S if phase == 0 else DISPENSE_STOP_S
        total += stops([(c, *p[phase]) for c, p in zip(channels, places) if phase < len(p)], stop)
    return total


def candidates(places, rows):
    n = len(places)
    found = []
    if len(set(rows)) == n and all(0 <= r < 8 for r in rows):
        found.append(list(rows))
    for phase in range(max(len(p) for p in places)):
        order = sorted(range(n), key=lambda i: (-places[i][min(phase, len(places[i]) - 1)][1], i))
        channels = [0] * n
        for rank, i in enumerate(order):
            channels[i] = rank
        found.append(channels)
        dest_rows = [round((places[i][min(phase, len(places[i]) - 1)][1])) for i in range(n)]
        del dest_rows
    order = topo(places)
    if order is not None:
        channels = [0] * n
        for rank, i in enumerate(order):
            channels[i] = rank
        found.append(channels)
    return found


def topo(places):
    """A channel order that keeps Y falling along the channels at every shared X."""
    n = len(places)
    after = {i: set() for i in range(n)}
    for phase in range(max(len(p) for p in places)):
        for i in range(n):
            for j in range(n):
                if i != j and phase < len(places[i]) and phase < len(places[j]):
                    (xi, yi), (xj, yj) = places[i][phase], places[j][phase]
                    if xi == xj and yi > yj:
                        after[i].add(j)
    order, seen, state = [], set(), {}
    indegree = {i: 0 for i in range(n)}
    for i in after:
        for j in after[i]:
            indegree[j] += 1
    ready = sorted((i for i in range(n) if indegree[i] == 0), key=lambda i: -places[i][0][1])
    while ready:
        i = ready.pop(0)
        order.append(i)
        for j in after[i]:
            indegree[j] -= 1
            if indegree[j] == 0:
                ready.append(j)
        ready.sort(key=lambda i: -places[i][0][1])
    return order if len(order) == n else None


def row_of(ref):
    name = ref.split(":")[1]
    return "ABCDEFGHIJKLMNOP".index(name[0]) if name[0] in "ABCDEFGHIJKLMNOP" else 0


def best_channels(places, rows):
    best = None
    for channels in candidates(places, rows):
        cost = round_cost(places, channels)
        if best is None or cost < best[0]:
            best = (cost, channels)
    return best


def exact_channels(places):
    """Distinct channels so that every shared X keeps Y falling 9 mm per channel; None if impossible."""
    n = len(places)
    pairs = []
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            for phase in range(min(len(places[i]), len(places[j]))):
                (xi, yi), (xj, yj) = places[i][phase], places[j][phase]
                if xi == xj and yi >= yj:
                    pairs.append((i, j, yi - yj))
    need = {i: [] for i in range(n)}
    for i, j, gap in pairs:
        need[i].append((j, gap))
        need[j].append((i, -gap))
    order = sorted(range(n), key=lambda i: -places[i][0][1])
    channel = {}

    def fits(i, c):
        for j, gap in need[i]:
            if j in channel:
                d = channel[j] - c
                if gap > 0 and not (d > 0 and gap >= 90 * d - 1):
                    return False
                if gap < 0 and not (d < 0 and -gap >= 90 * -d - 1):
                    return False
                if gap == 0:
                    return False
        return True

    def search(k, used):
        if k == n:
            return True
        i = order[k]
        for c in range(8):
            if c not in used and fits(i, c):
                channel[i] = c
                if search(k + 1, used | {c}):
                    return True
                del channel[i]
        return False

    return [channel[i] for i in range(n)] if search(0, frozenset()) else None


def batch_cost(places):
    exact = exact_channels(places)
    if exact is not None:
        return round_cost(places, exact), exact
    return best_channels(places, [0] * len(places))


def best_assignment(bot, jobs):
    places = [phases(bot, job) for job in jobs]
    _, channels = batch_cost(places)
    return sorted(zip(channels, jobs), key=lambda pair: pair[0])


def quick_cost(places):
    """Seconds of X stops, ignoring channel spacing."""
    total = 0.0
    depth = max(len(p) for p in places)
    for phase in range(depth):
        stop = ASPIRATE_STOP_S if phase == 0 else DISPENSE_STOP_S
        total += stop * len({p[phase][0] for p in places if phase < len(p)})
    return total


SEARCH = {"iterations": 100000, "restarts": 4, "polish": 40000, "seed": 0, "extra_rounds": 0}
ROUND_S = 8.0


def plan_batches(bot, jobs, avail, width):
    """Split the jobs that can run now into rounds of up to `width`."""
    seed = avail[0]
    sig = signature(jobs[seed])
    pool = [n for n in avail if signature(jobs[n]) == sig]
    if len(pool) <= width:
        rest = [n for n in avail if n not in pool]
        return [pool + rest[: width - len(pool)]]
    places = {n: phases(bot, jobs[n]) for n in pool}
    count = -(-len(pool) // width) + SEARCH["extra_rounds"]
    size = -(-len(pool) // count)
    start = [pool[k : k + size] for k in range(0, len(pool), size)]
    start += [[] for _ in range(count - len(start))]
    by_source = sorted(pool, key=lambda n: (places[n][0][0], -places[n][0][1]))
    grouped = [by_source[k : k + size] for k in range(0, len(pool), size)]
    grouped += [[] for _ in range(count - len(grouped))]
    memo = {}
    iterations, restarts, polish, base = (SEARCH[k] for k in ("iterations", "restarts", "polish", "seed"))

    def quick(batch):
        return quick_cost([places[n] for n in batch]) + ROUND_S if batch else 0.0

    def full(batch):
        key = frozenset(batch)
        if key not in memo:
            memo[key] = batch_cost([places[n] for n in sorted(batch)])[0] + ROUND_S if batch else 0.0
        return memo[key]

    anneal.width = width
    plain = (sum(full(b) for b in start), start)
    best = min(plain, (sum(full(b) for b in grouped), grouped), key=lambda pair: pair[0])
    for seed_ in range(restarts):
        rough = anneal(grouped if seed_ % 2 else start, quick, (float("inf"), start), random.Random(base + seed_), iterations)
        tuned = anneal(rough[1], full, (sum(full(b) for b in rough[1]), rough[1]), random.Random(base + seed_), polish, 1.5)
        best = min(best, tuned, key=lambda pair: pair[0])
    best = min(best, anneal(best[1], full, best, random.Random(base + 7), polish, 0.8), key=lambda pair: pair[0])
    if SEARCH.get("columns"):
        best = anneal_columns(best[1], full, best, random.Random(base + 11), SEARCH["columns"], places)
    return [sorted(b) for b in best[1] if b]


def anneal(start, cost, best, rng, iterations, temperature=2.5):
    batches = [list(b) for b in start]
    costs = [cost(b) for b in batches]
    total = sum(costs)
    width = getattr(anneal, "width", 8)
    for step in range(iterations):
        a, b = rng.sample(range(len(batches)), 2)
        if not batches[a]:
            continue
        new_a, new_b = list(batches[a]), list(batches[b])
        i = rng.randrange(len(new_a))
        if len(new_b) < width and (not new_b or rng.random() < 0.3):
            new_b.append(new_a.pop(i))
        else:
            j = rng.randrange(len(new_b))
            new_a[i], new_b[j] = new_b[j], new_a[i]
        ca, cb = cost(new_a), cost(new_b)
        delta = ca + cb - costs[a] - costs[b]
        t = temperature * (1 - step / iterations) + 1e-3
        if delta <= 0 or rng.random() < math.exp(-delta / t):
            batches[a], batches[b], costs[a], costs[b] = new_a, new_b, ca, cb
            total += delta
            if total < best[0] - 1e-9:
                best = (total, [list(b) for b in batches])
    return best


def anneal_columns(start, cost, best, rng, iterations, places, temperature=1.0):
    """Swaps that bring a job into a round already stopping at one of its columns."""
    batches = [list(b) for b in start]
    costs = [cost(b) for b in batches]
    total = sum(costs)
    where = {n: k for k, b in enumerate(batches) for n in b}
    sharing = {}
    for n, p in places.items():
        for phase, (x, _) in enumerate(p):
            sharing.setdefault((phase, x), []).append(n)
    for step in range(iterations):
        a = rng.randrange(len(batches))
        if not batches[a]:
            continue
        stops = {}
        for n in batches[a]:
            for phase, (x, _) in enumerate(places[n]):
                stops.setdefault((phase, x), []).append(n)
        lonely = [n for n in batches[a] if any(len(stops[(ph, x)]) == 1 for ph, (x, _) in enumerate(places[n]))]
        i = rng.choice(lonely or batches[a])
        column = rng.choice(list(stops))
        others = [n for n in sharing[column] if where[n] != a]
        if not others:
            continue
        j = rng.choice(others)
        b = where[j]
        new_a = [j if n == i else n for n in batches[a]]
        new_b = [i if n == j else n for n in batches[b]]
        ca, cb = cost(new_a), cost(new_b)
        delta = ca + cb - costs[a] - costs[b]
        t = temperature * (1 - step / iterations) + 1e-3
        if delta <= 0 or rng.random() < math.exp(-delta / t):
            batches[a], batches[b], costs[a], costs[b] = new_a, new_b, ca, cb
            where[i], where[j] = b, a
            total += delta
            if total < best[0] - 1e-9:
                best = (total, [list(b) for b in batches])
    return best


VARIANTS = [("large", True), ("small", True), ("large", False)]
TWEAKS = [{"mix_frac": 1.0, "small_first": False}, {"mix_frac": 0.91, "small_first": False}, {"mix_frac": 0.91, "small_first": True}]


def solve(loaded, verbose=False):
    best = None
    candidates = []
    for tweak in TWEAKS:
        OPTIONS.update(tweak)
        for prefer, back in VARIANTS if tweak is TWEAKS[0] else VARIANTS[:1]:
            name = f"{prefer}/{'rack' if back else 'waste'}/mix{tweak['mix_frac']}/{'small' if tweak['small_first'] else 'big'}first"
            try:
                candidates.append((name, solve_variant(loaded, prefer, back)))
            except (ValueError, KeyError, IndexError) as error:
                if verbose:
                    print(f"  {name}: {error}", file=sys.stderr)
    OPTIONS.update(TWEAKS[0])
    candidates.append(("starter", starter.solve(loaded, loaded["deck"])))
    for name, program in candidates:
        report, _ = grade.score(loaded, program)
        if verbose:
            print(f"  {name}: {report['seconds'] if report['valid'] else report['failures'][:3]}", file=sys.stderr)
        if report["valid"] and (best is None or report["seconds"] < best[0]):
            best = (report["seconds"], program, name)
    return best


if __name__ == "__main__":
    loaded = protocol.load(sys.argv[1])
    result = solve(loaded, verbose="-v" in sys.argv)
    if result is None:
        sys.exit("no valid program")
    print(f"best: {result[2]} {result[0]:.1f} s", file=sys.stderr)
    print(result[1], end="")
