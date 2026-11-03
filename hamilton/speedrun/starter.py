"""The starter solver: up to eight steps at a time, one fresh tip per step.

Each round takes up to eight ready steps: steps whose earlier steps on the
same wells and plates are all done, so they may run together. Each one gets
its own channel and tip. All channels aspirate, all dispense, and all tips go
to the waste. A mix that follows a transfer into its well mixes in that
transfer's dispense. A move runs on its own when it is the first ready step.

    python3 -m speedrun starter protocols/serial-dilution.json > program.txt
"""

from . import protocol
from .robot import Robot


def solve(loaded, deck):
    steps = loaded["steps"]
    before = protocol.predecessors(steps)
    robot = Robot(deck)
    done = set()
    while len(done) < len(steps):
        ready = [i for i in range(len(steps)) if i not in done and done.issuperset(before[i])]
        if steps[ready[0]]["op"] == "move":
            robot.move(steps[ready[0]]["plate"], steps[ready[0]]["to"])
            done.add(ready[0])
            continue
        jobs = [i for i in ready if steps[i]["op"] != "move"][:8]
        mixes = following_mixes(steps, before, done, jobs)
        done.update(jobs, mixes.values())
        run(robot, [steps[i] for i in jobs], [steps[mixes[i]] if i in mixes else None for i in jobs])
    return robot.program()


def following_mixes(steps, before, done, jobs):
    """Map a transfer in jobs to the mix step that only waits for it."""
    mixes = {}
    for i, step in enumerate(steps):
        waiting = set(before[i]) - done
        if step["op"] == "mix" and len(waiting) == 1:
            (transfer,) = waiting
            if transfer in jobs and steps[transfer].get("to") == step["well"] and transfer not in mixes:
                mixes[transfer] = i
    return mixes


def run(robot, steps, mixes):
    channels = range(len(steps))
    robot.pick_up(channels, [max(step["ul"], mix["ul"] if mix else 0) for step, mix in zip(steps, mixes)])
    transfers = [channel for channel in channels if steps[channel]["op"] == "transfer"]
    if transfers:
        robot.aspirate([steps[c]["from"] for c in transfers], [steps[c]["ul"] for c in transfers], transfers)
    for channel in channels:
        if steps[channel]["op"] == "mix":
            robot.mix([steps[channel]["well"]], steps[channel]["ul"], steps[channel]["cycles"], [channel])
    by_mix = {}
    for channel in transfers:
        mix = mixes[channel] and (mixes[channel]["ul"], mixes[channel]["cycles"])
        by_mix.setdefault(mix, []).append(channel)
    for mix, group in by_mix.items():
        robot.dispense([steps[c]["to"] for c in group], [steps[c]["ul"] for c in group], group, mix=mix)
    robot.eject()
