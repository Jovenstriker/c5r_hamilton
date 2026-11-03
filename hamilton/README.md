# Hamilton Speedrun

Our Hamilton STARlet is a liquid-handling robot: eight pipetting channels on
one moving arm, and a plate gripper called the iSWAP. You get a protocol, a
list of liquid transfers, mixes and plate moves. You write the firmware
program that performs it.

A digital twin of the robot replays your program, checks every command and
times it. The fastest valid program wins. The fastest valid programs in the
final run on our real robot.

## Quickstart

Run these from this folder:

```sh
python3 -m speedrun solve protocols/serial-dilution.json > program.txt
python3 -m speedrun score protocols/serial-dilution.json program.txt
python3 -m speedrun score protocols/serial-dilution.json program.txt --trace trace.jsonl --json > report.json
```

`solve` is the reference solver (`speedrun/reference.py`). Its programs are
valid and slow: one channel, one fresh tip per step. Read one: it is the
quickest way to learn the command format.

## Write a solver

```text
protocol JSON  ->  your solver  ->  program.txt  ->  score (twin + grader)
```

A solver reads a protocol and its deck and prints a program. The quickest
start is the starter solver, `speedrun/starter.py`: about 60 lines, up to
eight steps at a time, valid on every protocol and many times faster than
the reference. Copy it and improve it.

```sh
python3 -m speedrun starter protocols/serial-dilution.json > program.txt
python3 -m speedrun score protocols/serial-dilution.json program.txt
```

It builds programs with `speedrun/robot.py`, which writes the firmware for
you: units, channel lists, flows, and heights that pass the rules below. It
tracks the liquid in every well and tip, and splits a step into several
commands when the channels cannot reach their wells at once.

| `Robot` method | What it does |
| --- | --- |
| `Robot(deck)` | Start with no tips; `loaded["deck"]` from `protocol.load` |
| `pick_up(channels, ul)` | One tip per channel (0 to 7, back to front), from the smallest rack whose tips hold `ul` |
| `aspirate(wells, ul, channels, doses=1)` | Draw `ul` from each well; `doses=n` draws enough for `n` dispenses of `ul` |
| `dispense(wells, ul, channels, mix=None, jet=False)` | Dispense into each well, touching the liquid; `mix=(ul, cycles)` mixes after; `jet=True` stays above the liquid |
| `mix(wells, ul, cycles, channels)` | Mix each well |
| `empty(channels)` | Put what is left in each tip back into its source well |
| `eject(channels)` | Drop tips into the waste |
| `move(plate, nest)` | Carry a plate with the iSWAP |
| `program()` | The program text |

`wells` is a list, one well per channel; `channels` defaults to 0, 1, 2, ...
and `ul` is one number or one per well. Some mistakes, such as aspirating
more than a well holds, raise `ValueError`; `score` has the final word.

Where the starter loses time:

- Tips: every step gets a fresh tip. Rule 5 says when one tip may serve
  several steps.
- Commands: one aspirate can feed several `jet` dispenses (`doses`); the
  starter aspirates once for every dispense.
- Order: the starter takes ready steps in protocol order. Another order may
  fill all eight channels more often, or keep them on one column.
- Read your trace: which commands take the most `ms`?

## Folder

| Path | What it is |
| --- | --- |
| `protocols/` | The protocols to solve |
| `decks/` | The deck each protocol starts from, as the twin loads it |
| `speedrun/` | Protocol model, grader, reference and starter solvers, `Robot` and CLI |
| `bin/` | The twin, one binary per platform |
| `viewer/hamilton.html` | Replays a trace in your browser |
| `FIRMWARE.md` | Firmware primer: commands, fields, coordinates |

## Protocols

| Protocol | What it does | Reference |
| --- | --- | --- |
| `cherry-pick` | Moves 64 samples, each its own volume, to scattered wells of a buffer plate | 1189.7 s |
| `magnet-cleanup` | Pulls 48 samples onto a magnet, removes the supernatant, resuspends in fresh buffer | 2853.8 s |
| `reagent-add` | Adds 30 µL of buffer to every well of an assay plate from one reservoir column | 1787.6 s |
| `replicate` | Copies 20 µL of each of 96 samples into the same well of two plates | 3533.1 s |
| `serial-dilution` | Dilutes eight samples 1:3 across a plate, eleven times, mixing each new well | 3162.7 s |

"Reference" is the score of the reference solver's program.

```json
{
  "id": "serial-dilution",
  "title": "Serial dilution",
  "summary": "Dilute eight samples 1:3 across a plate, eleven times, mixing each new well.",
  "deck": "decks/serial-dilution.json",
  "steps": [
    {"op": "transfer", "from": "dilution:A1", "to": "dilution:A2", "ul": 50.0},
    {"op": "mix", "well": "dilution:A2", "ul": 40.0, "cycles": 3},
    ...
  ]
}
```

A move looks like `{"op": "move", "plate": "beads", "to": "magnet/1"}`.

- `deck` is relative to this folder. Wells are written `plate:well`.
- `transfer` moves `ul` microliters of liquid from one well to another.
- `mix` mixes a well by pipetting `ul` up and down at least `cycles` times.
- `move` carries a plate with the iSWAP to another nest.
- The steps are listed in a valid order. You may reorder steps that share no
  well and no plate. Steps that touch a common well, and a `move` and any
  step on that plate, keep their relative order.
- The expected end state follows from the deck and the steps: liquids mix
  ideally, and every well holds each component in proportion. Nothing is
  hand-written.

## The deck

Every protocol uses the same carriers, so the robot's setup never changes:

- a plate carrier on the left (rail 1) with five nests, `plate_carrier/1`
  at the front to `plate_carrier/5` at the back. A plate magnet stands on
  `plate_carrier/5`; a plate on it sits at nest `magnet/1`;
- a tip carrier in the middle (rail 14). In the public decks it holds 50 µL
  tips at `tip_carrier/1` and `tip_carrier/2` and 1000 µL tips at
  `tip_carrier/3`;
- a module carrier (rail 24) that the protocols do not use, and the tip
  waste at the right end of the deck.

Protocols vary only the plates, their liquids and the tip racks, so a new
protocol may bring other racks on the tip carrier. Read them from the deck's
`speedrun.tips` rather than assuming these. A deck file holds:

- `deck`: carriers, nests, plates with their wells and liquids, tip racks and
  the plunger-to-liquid calibration;
- `world`: fixed furniture around the deck, for collision checks;
- `seat_contacts`: how plates sit on their nests;
- `speedrun`: the shortcuts you need: tip racks with their positions and
  `C0TT` definitions, the tip waste, and the published iSWAP templates.

See [FIRMWARE.md](FIRMWARE.md) for how to find wells, tips and heights.

## Programs

A program is a text file with one firmware command per line. Blank lines and
lines starting with `#` are ignored.

```text
# pick up eight 50 uL tips, column 1 of tips_50_a
C0TTid0001tt02tf1tl0424tv00600tg2tu0
C0TPid0002xp04104&yp1458 1368 1278 1188 1098 1008 0918 0828tm1&tt02tp2250tz2170th2450td1
# drop them in the waste
C0TRid0003xp08000&yp4050 3782 3514 3246 2978 2710 2442 2175tp1970tz1870th2450te2450tm1&ti0
```

- The file is UTF-8 text with LF or CRLF line endings. Command lines are
  printable ASCII, at most 2048 characters each; comments may hold any text.
  A program has at most 2000 commands.
- The robot is initialized before your first line: channels raised, no tips,
  iSWAP parked.
- The program is open loop. The same file runs on the real robot after it
  passes the twin, so it cannot react to replies.
- Allowed commands: `C0TT C0TP C0TR C0AS C0DS C0PP C0PR C0PG` and the
  read-only queries `C0RF C0RX C0RY C0RZ C0RT C0QP`. Everything else is
  rejected, including plain moves, initialization, settings and service
  commands.

[FIRMWARE.md](FIRMWARE.md) explains every field.

## Rules

Any failure makes the run invalid. Volumes compare within 0.2 µL + 1%.

1. **Clean replies.** Every reply is `er00`: no refusals, no error replies, no
   `collision` event.
2. **Commands.** Only the allowed commands above, each written as opcode,
   `id` and fields, with no request id used twice. Every `C0PP` and `C0PR`
   equals one of the deck's published iSWAP templates, field for field,
   ignoring `id`. A `C0PP` is followed at once by a `C0PR`, and that `C0PR`
   by a `C0PG`. Every `C0TT` equals the `define` of a rack in the deck's
   `speedrun.tips`, ignoring `id`, and every pickup uses the tip type of the
   rack it picks from. Fields the twin does not model keep a fixed value on
   every channel, idle ones included:

   | Command | Fixed fields |
   | --- | --- |
   | `C0TP` | `td1` |
   | `C0AS` | `ch000 ld00 lk0 ik0000 sd0500 se0500 sz0300 io0000`, and `il00000 in0000` if you send them |
   | `C0DS` | `dj00 ss1000 ip0000 it0` |
   | `C0AS`, `C0DS` | `ll1 lv1 de0010 gi000 gj0 gk0` |

   Any other value fails, for example `line 7: ss must be ss1000: the twin
   does not model it`.
3. **Bounds.** List fields are checked on every channel, idle ones included:

   | Field | Bound |
   | --- | --- |
   | `th`, `te` (traverse heights) | at least 2450 (245.0 mm) |
   | `lm` (height mode) | `0`: a fixed height, no liquid level detection |
   | `at` (aspiration type) | `0` or `1` |
   | `mc` (mix cycles) | at most 20; at most 2000 in the program, summed over active channels |
   | `tp`, `tz` of `C0TP`, and of `C0TR` with `ti1` | the rack's `top_z_mm` and `top_z_mm` - `stroke_mm`, within 0.05 mm |
   | `tp`, `tz` of `C0TR` into the waste | `ti0` and the waste's `top_z_mm` and `bottom_z_mm`, within 0.5 mm |

   Flows are capped by the tip on the channel, in 0.1 µL/s:

   | Field | 50 µL tip | 1000 µL tip or idle channel |
   | --- | --- | --- |
   | `as` (aspirate flow) | 1000 | 2500 |
   | `ms` (mix flow) | 1000 | 2500 |
   | `ds` (dispense flow) | 2000 | 4000 |

   The mix cycle limits are checked before the twin runs.
4. **Wells.** In every `C0AS` and `C0DS`, each active channel's `xp`, `yp`
   is within 0.3 mm of a well center, with the plate where it stands now.
   Its `zl` and `zx` are at least 1.0 mm above that well's bottom. A `C0AS`
   aspirates at `zl`, at or below the well's liquid surface after that
   aspiration, minus 0.5 mm. The surface is the well bottom plus the liquid
   height (volume / area for a straight well), tracked from the twin's
   events. The twin itself does not check this; the grader does.
5. **Tips.** From pickup to eject or return, a tip aspirates from one well
   only. A dispense touches the well's liquid unless the tip end is at least
   1.0 mm above the liquid surface after the dispense (a jet); a mix touches
   it when its `mix` event is `in_liquid`. A tip may touch the liquid of a
   second well only if the protocol has a transfer from the first well the
   tip touched into that second well. After that it may only dispense or mix
   in the second well, with no jets elsewhere, and may not aspirate again.
   This holds for fresh tips too: a tip that mixes one well and then another
   needs such a transfer between them. A rack spot is never picked twice. A
   discarded tip (`ti0`) is ejected at the deck's `speedrun.waste`, within
   0.5 mm: `xp` at its `x_mm` and each channel's `yp` at that channel's
   `y_mm`. A returned tip (`ti1`) goes into an empty spot: one your program
   has emptied or one the deck lists as `missing`.
6. **Plunger.** From pickup to eject or return, a tip holds the sum of its
   `C0AS` `av` minus the sum of its `C0DS` `dv`. No dispense may take that
   below -0.1 µL. A `dm4` dispense (empty the tip) may use any `dv` up to
   what the tip holds and leaves the tip at 0. To dispense several times from
   one aspiration, aspirate the total you need and empty the rest back into
   the source well ([FIRMWARE.md](FIRMWARE.md#volumes)).
7. **Mixes.** Each `mix` step needs a `mix` event on its well, in liquid, with
   at least the required cycles and at least 90% of the required volume. The
   tip stays in the liquid for the whole stroke: the top of the mix stroke is
   at least 0.5 mm below the surface that is left while the mix volume is in
   the tip ([FIRMWARE.md](FIRMWARE.md#mixing) shows how to find the top). The
   mix must happen when the well's total added and removed volumes equal
   those of all the steps before the mix step that touch that well. Liquid a
   tip puts back into the well it came from counts as never removed.
8. **Moves.** Each plate rests on the sequence of nests the protocol's moves
   give it, in order. At each move, every well on the plate has had exactly
   the volumes added and removed by the steps before that move.
9. **End state.** Every well holds each expected component within 0.2 µL + 1%
   and no unexpected component above 0.2 µL. Plates are on their final nests,
   no channel carries a tip, nothing is gripped and the iSWAP is parked.

## Score

```text
seconds = sum(ms) / 1000 + 0.8 x commands
```

`ms` is the twin's modeled machine time for each command. The 0.8 s per
command is the robot's measured per-command overhead, which the twin does
not model. `commands` counts every command line, queries included. Lower is
better; an invalid run has no score.

`score --json` prints:

```json
{"protocol": "serial-dilution", "valid": true, "seconds": 3162.74,
 "twin_seconds": 2669.14, "commands": 617, "failures": [], "trace": "trace.jsonl"}
```

`failures` names the line or step and the rule for each problem: the first
20, then `... and N more`. Without `--json`, an invalid run prints
`invalid: N problem(s)` and the same lines. `trace` is `null` when the twin
did not run, for example when the program has too many commands or mix
cycles.

`score` takes about 0.04 s per command on a laptop (about 25 s for a
reference program) and prints nothing until it is done. After 300 s it stops
the twin and the run is invalid. The site does the same, and may run slower
than your laptop.

On the site, a team may have 2 submissions scoring at a time and 30
submissions per 10 minutes. A third submission while two are scoring gets
`Wait for your earlier submissions to finish scoring.`

## The twin

`score` runs the twin for you and saves its trace next to your program
(`program.trace.jsonl`), or where `--trace` says, and prints `trace:` with
the path. When the twin does not run, there is no `trace:` line. To run the
twin directly:

```sh
bin/twin-macos-arm64 decks/serial-dilution.json < program.txt > trace.jsonl
```

Pick the binary for your machine: `twin-macos-arm64`, `twin-macos-x86_64`,
`twin-linux-x86_64` or `twin-linux-arm64`. `score` finds it on its own; set
`SPEEDRUN_TWIN` to use another path.

The twin runs a fixed initialization (not timed), then your program. On the
robot the same initialization runs before your program starts. The twin
prints one JSON object per command:

```json
{"line": 3, "wire": "C0AS...", "reply": "C0ASid0003er00/00", "refused": false,
 "error": false, "ms": 4173, "events": [...], "rested": [...]}
```

- `line` is the line number in your file; `wire` the command; `reply` the
  robot's answer.
- `refused` and `error` flag a rejected command or an error reply.
- `ms` is the command's machine time.
- `events` lists what happened: `collision`, `tip_pickup`, `tip_return`,
  `aspirate`, `dispense` and `mix`, with `channel` (0 to 7). Tip events name
  the `rack` and `spot`; liquid events name the `plate`, `well` and
  `microliters`, and `dispense` and `mix` say `in_liquid`; `mix` also has
  `cycles`.
- `rested` lists plates that came to rest at a new position, with `plate`
  and `origin_mm`.

The last line is `{"final": ...}`: the deck, wells, tips and gripper at the end.

## Viewer

Open `viewer/hamilton.html` in a browser. Add the deck file, a trace
(`.jsonl`) and optionally the `score --json` report: choose them one at a
time, or drag them onto the page together. It replays the run step by step
and marks the failures. Nothing is uploaded.

## Where the seconds go

The reference program is far from the best. Some questions worth asking:

- Each command costs 0.8 s before it moves at all. When can one command do
  the work of two?
- Eight channels share one arm. When do they work together, and when do
  they wait on each other?
- Which commands take longest in your trace, and is that time motion or
  liquid handling?
- How far does the arm travel, and how high does it go, between two
  commands?
- The rules bound flow rates and heights. Where does your program sit inside
  those bounds?
- Steps that share nothing may be reordered. What order keeps the arm busy?
- Read rule 5 carefully: it says exactly what a tip may and may not do.

Validate locally before you submit; the server runs the same grader.

## Troubleshooting

- `er01/3x` replies are format problems: a missing field, a wrong width, an
  `&` in the wrong place. `er99/00` replies come from a channel or the
  iSWAP. [FIRMWARE.md](FIRMWARE.md#replies) lists the ones you meet first.
- macOS may block the twin the first time: a "cannot be opened" dialog, or
  `score` reports "twin failed" with no detail. Run
  `xattr -dr com.apple.quarantine .` in the kit folder once.
- "cannot run the twin: Permission denied": the zip tool dropped the
  executable bit. Run `chmod +x bin/*`.
- After the first error, later commands often fail too. Fix the first one.

## The final

Near the end of the evening the site releases a new protocol on the same
carriers. Its plates, tubes, tip racks and rack names can differ, so a solver
should read everything from the deck file. Run your solver on it and submit
its program before the deadline. It is graded in the twin with these rules, and the fastest valid
programs then run on our Hamilton. See the main [README](../README.md#the-final).
