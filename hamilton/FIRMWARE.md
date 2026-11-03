# Firmware primer

Your program is a list of firmware commands for the STARlet's master
controller, one per line. This primer covers the commands the grader allows
and how their fields work. When in doubt, ask the twin: it answers every line
the way the robot does.

## Anatomy of a command

```text
C0PGid0001th2840
```

| Part | Meaning |
| --- | --- |
| `C0` | The master controller. Every line in your program starts with `C0`. |
| `PG` | The command: park the iSWAP. |
| `id0001` | Request number: `id` and four digits. The reply repeats it. Use each number once; counting up is easiest. |
| `th2840` | Field `th` with value `2840`: a traverse height of 284.0 mm. |

A field is two lowercase letters and a fixed number of digits. Keep every
digit, including leading zeros, and keep the fields in the order the templates
below use. Fields are joined with no spaces.

The reply starts with the command and id, then `er00/00` on success:

```text
C0PGid0001er00/00
```

Anything else is a failure. After a failure the twin keeps going, but the
grader marks the run invalid. Fix the first failure first: later ones often
follow from it.

## Replies

`er01` replies are format errors. The command did not run.

| Reply | Meaning |
| --- | --- |
| `er01/30` | Unknown command. |
| `er01/31` | Unknown or repeated field, or an `&` after a full list of eight. |
| `er01/32` | Value out of range, such as an X the arm cannot reach, a height above the channel's top or two channels less than 8.9 mm apart in Y. |
| `er01/33` | Missing or badly formatted field, such as a value with the wrong number of digits. |
| `er01/35` | Bad request number, such as `idabcd`. |

A line too short to hold an opcode and an `id` with four digits is refused
with no reply.

`er99/00` replies come from a module. In `er99/00 P102/62`, `P1` is channel
1 (`P1` to `P8` are the channels, `R0` is the iSWAP), `02` the error and
`62` the detail. These are the ones you meet first:

| Reply | Meaning | Check |
| --- | --- | --- |
| `P108/00` | The channel has no tip. | A `C0TP` comes first, with this channel in its `tm`. |
| `P107/76` | The channel already carries a tip. | Eject (`C0TR`) before the next pickup. |
| `P108/75` | No tip was picked up. | X, Y of the spot, that the spot was not used before, `tp` and `tz` from `speedrun.tips`. |
| `P102/62` | The tip hit something, such as a well bottom or the edge of the waste. | `zl` and `zx` against the well bottom; the X, Y of an eject. |
| `P106/70` | No well under the tip. | X, Y against the plate's current origin; after a move, the template's `origin_mm`. |
| `P106/71` | The tip is above the liquid, the well has too little, or it would overflow. | The height against the liquid surface; the volumes. |
| `P102/53` | More than the tip holds. | `av` and `mv` are plunger volumes; see "Volumes". |
| `P102/54` | A dispense of more than the tip holds now. | `dv` against what the tip took up. |
| `P101/32` | A `C0TP` whose `th` is above the channel's top with the new tip. | `th` against the top; see "Coordinates". |
| `R007/00` | The iSWAP cannot grip while a channel carries a tip. | Eject every tip before `C0PP`. |
| `R023/96` | `C0PR` with no plate in the gripper. | A `C0PP` comes first. |
| `R022/97` | `C0PP` while the gripper holds a plate. | Put it down with `C0PR` first. |

## Units

| Quantity | Unit | Example |
| --- | --- | --- |
| Position, height, distance | 0.1 mm | `xp03433` = 343.3 mm |
| Volume | 0.1 µL | `av01096` = 109.6 µL |
| Flow rate | 0.1 µL/s | `as2500` = 250 µL/s |
| Settling time | 0.1 s | `wt10` = 1.0 s |

## Coordinates

The deck file (`decks/*.json`) and the firmware use the same frame, in mm in
the deck file and 0.1 mm on the wire.

- **X** runs left to right along the deck. Carriers sit on rails 22.5 mm
  apart; rail 1 is at X = 100.0.
- **Y** runs from the front of the deck to the back.
- **Z** runs up. The deck surface is at Z = 100.0; channels are fully raised
  at Z = 334.7.

The robot has eight pipetting channels on one arm and an iSWAP plate gripper.

- All eight channels share one X. Each has its own Y and Z.
- Channel 1 is at the back (largest Y) and channel 8 at the front. Neighbors
  stay at least 8.9 mm apart, so Y1 > Y2 > ... > Y8.
- A 96-well plate has a 9 mm well pitch, so the eight channels fit one plate
  column exactly: A1 to H1 at once.
- The arm reaches X 95.0 to 800.0.
- A channel's Z is its lowest point: the end of the tip when it carries one,
  the nozzle when it does not. It ranges from 136.0 to the channel's top:
  334.7 minus the tip length (`tl`), so 292.3 with 50 µL tips and 247.6 with
  1000 µL tips. With 1000 µL tips, `th` and `te` are therefore `2450` to
  `2476`.
- Each channel reaches its own range of Y, in mm:

| Channel | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Lowest Y | 68.8 | 59.8 | 50.8 | 41.8 | 32.8 | 23.8 | 14.8 | 5.9 |
| Highest Y | 606.5 | 597.6 | 588.6 | 579.6 | 570.6 | 561.6 | 552.6 | 543.6 |

### Finding things on the deck

A deck file is a JSON object with `deck`, `world`, `seat_contacts` and
`speedrun`. The parts you need:

- `deck.plates[]`: each plate has `name`, `origin_mm` (where it sits now) and
  `wells[]`. Each well has `name`, `center_mm` (relative to the plate's
  origin), `bottom_z_mm`, `area_mm2`, `capacity_microliters`,
  `volume_microliters` and `components_microliters`.
- `deck.correction_curves[]`: how liquid volume maps to plunger volume.
- `speedrun.tips.<rack>`: each tip rack's `nest`, tip size `volume_ul`, its
  `define` command (a `C0TT` without the `id`), `a1_mm` (X, Y of tip A1),
  `pitch_mm`, `top_z_mm` (tip top) and `stroke_mm` (pickup stroke).
- `speedrun.waste`: the tip waste, with `x_mm`, one `y_mm` per channel,
  `top_z_mm` and `bottom_z_mm`.
- `speedrun.iswap_templates[]`: the plate moves the grader accepts. Each has
  `plate`, `nest`, `origin_mm` (where the plate sits on that nest), `pick`
  (a `C0PP` without the `id`) and `place` (a `C0PR` without the `id`).

From these:

- Well X, Y = plate origin + well `center_mm`. After a move, use the
  template's `origin_mm` as the plate origin.
- Well bottom Z = plate origin Z + well `bottom_z_mm`.
- Liquid surface Z = well bottom Z + liquid height. For a straight well the
  height is volume / `area_mm2`; a well with a `container_profile` follows
  its shape (see `liquid_height` in `speedrun/protocol.py`).
- Tip X, Y: start at `a1_mm`; each column adds `pitch_mm` to X, each row
  (A to H) subtracts `pitch_mm` from Y.

Write wire values in 0.1 mm, rounded.

## Channel lists

Fields that apply per channel take a list of values separated by single
spaces, in channel order 1 to 8. A full list of eight values ends without a
marker. A shorter list ends with `&`, and its last value repeats for the
remaining channels.

```text
xp03433&                                    all eight channels at X 343.3
yp1459 1369 1279 1189 1099 1009 0919 0829   eight Y values, 9.0 mm apart
tm1 0&                                      channel 1 active, 2-8 idle
tm1&                                        all eight active
tm0 1 1 0&                                  channels 2 and 3 active
```

An `&` after a full list of eight is a format error (`er01/31` or
`er01/33`).

`tm` selects the active channels. The firmware ignores the positions and
volumes of idle channels, but still checks the range of their `lm`, `dm`,
`it`, `ll`, `lv`, `mv` and `mc`, and the grader's bounds and fixed values
apply to every value in a list. A list that ends with `&` gives idle
channels the active value, which is always safe.

An idle channel does not move to its `yp`. It stays where it is until an
active neighbor comes closer than 8.9 mm, and then it is pushed along ahead
of that neighbor.

Channels may have different `xp` values in one command. The arm then serves
them one X after another.

Fields that apply to the whole command take one value and no `&`: `tt`,
`tp`, `tz`, `th`, `te`, `td` and `ti` of `C0TP` and `C0TR`, and `th`, `te`,
`dj`, `gj` and `gk` of `C0AS` and `C0DS`.

## Commands you may use

| Command | Does |
| --- | --- |
| `C0TT` | Defines a tip type. |
| `C0TP` | Picks up tips. |
| `C0TR` | Ejects tips into the waste or back into their rack. |
| `C0AS` | Aspirates, optionally mixing first. |
| `C0DS` | Dispenses, optionally mixing after. |
| `C0PP` | Grips a plate with the iSWAP. |
| `C0PR` | Releases the held plate at a nest. |
| `C0PG` | Parks the iSWAP. |
| `C0RF` `C0RX` `C0RY` `C0RZ` `C0RT` `C0QP` | Read firmware version, arm X, channel Y, channel Z, fitted tips, plate in gripper. |

Every other command is rejected by the grader, even if the twin accepts it.
The pipetting commands move where they need to go. Initialization is done
for you: the twin runs a fixed setup before your first line, and the robot
does the same.

Every command costs time. Read-only commands do not change anything, and
your program is open loop, so they only make sense while you debug.

### `C0TT`: define a tip type

```text
C0TTid0002tt02tf1tl0424tv00600tg2tu0
```

| Field | Meaning | Range |
| --- | --- | --- |
| `tt` | Tip type number. `C0TP` refers to it. | As in the rack's `define` |
| `tf` | `1` for a filter tip, else `0`. | As in the rack's `define` |
| `tl` | Tip length. | As in the rack's `define` |
| `tv` | Tip capacity. | As in the rack's `define` |
| `tg` | Collar type. | As in the rack's `define` |
| `tu` | Pickup method. | As in the rack's `define` |

Copy a rack's `define` from the deck file exactly and insert your own `id`;
the grader accepts nothing else. Define each tip type once, before its first
pickup.

### `C0TP`: pick up tips

```text
C0TPid0003xp04104&yp1458&tm1 0&tt02tp2250tz2170th2450td1
```

| Field | Meaning | Range |
| --- | --- | --- |
| `xp` | X of each channel's tip. | `00950` to `08000` |
| `yp` | Y of each channel's tip. | Per channel; see "Coordinates" |
| `tm` | Active channels. | `0` or `1` per channel |
| `tt` | Tip type defined with `C0TT`. | `00` to `99` |
| `tp` | Nozzle Z where the pickup stroke starts. | The rack's `top_z_mm`, within 0.05 mm |
| `tz` | Nozzle Z where it ends. | `top_z_mm` minus `stroke_mm`, within 0.05 mm |
| `th` | Height of the tip ends after pickup. | `2450` to the channel's top with the new tip |
| `td` | Pickup option. | Fixed `td1` |

For `top_z_mm` 224.95 and `stroke_mm` 8.0, that is `tp2250` and `tz2170`.

This example picks tip A1 of a 50 µL rack on channel 1. A channel that
already carries a tip refuses. Each rack spot can be picked once.

### `C0AS`: aspirate

```text
C0ASid0004at0&tm1 0&xp01184&yp2417&th2450te2450lp1980&ch000&zl1880&po0050&zu0000&zr00000&zx1880&ip0000&it0&fp0000&av00540&as1000&ta000&ba0000&oa000&lm0&ll1&lv1&zo005&ld00&de0010&wt00&mv00000&mc00&mp000&ms1000&mh0000&gi000&gj0gk0lk0&ik0000&sd0500&se0500&sz0300&io0000&
```

This example aspirates 50 µL of liquid (plunger 54.0 µL) with channel 1 from
well A1 of plate `dilution` in `decks/serial-dilution.json`, 2.0 mm above the
well bottom. The table lists every field in template order, with the values
the twin and the grader accept, in the units above. A fixed field does not
change the twin's motion, time or liquid, and any other value fails (rule
2). Every other field is yours to choose within its range.

| Field | Meaning | Range |
| --- | --- | --- |
| `at` | Aspiration type: `1` skips the blow-out air and over-aspirate volume. | `0` or `1` |
| `tm` | Active channels. | `0` or `1` per channel |
| `xp` | X of each channel's well. | With `yp`, within 0.3 mm of a well center (rule 4) |
| `yp` | Y of each channel's well. | With `xp`, within 0.3 mm of a well center |
| `th` | Traverse height: channels rise to it before moving. | `2450` to the channel's top |
| `te` | Height after the command. | `2450` to the channel's top |
| `lp` | Search start for liquid level detection; unused with `lm0`. | `1360` to `3347` |
| `ch` | Clot check height. | Fixed `ch000` |
| `zl` | Aspiration height. | Well bottom + 1.0 mm (rule 4) to the channel's top |
| `po` | Distance the tip pulls up after aspirating. | `0000` to the top minus `zl` |
| `zu` | Height of a tapered well bottom, for surface following; `0` for a straight well. | `0000` to `9999` |
| `zr` | Width of that tapered section at its bottom over its width at the top, x 10000. | `00000` to `10000` |
| `zx` | Lowest Z the tip may reach. | Well bottom + 1.0 mm (rule 4) to `3347` |
| `ip` | Immersion after liquid level detection; unused with `lm0`. | `0000` to `9999` |
| `it` | Direction of `ip`; unused with `lm0`. | `0` or `1` |
| `fp` | Distance the tip follows the surface down while aspirating. | `0000` up; below the well bottom the tip crashes (`P102/62`) |
| `av` | Plunger volume; see "Volumes". | `av` + `ta` + `oa` at most the tip's `tv` |
| `as` | Flow. | `0001` to `1000` with 50 µL tips, `2500` with 1000 µL tips (rule 3) |
| `ta` | Transport air drawn after aspirating. | `000` to `999`; see `av` |
| `ba` | Blow-out air drawn before aspirating. | `0000` to `9999` |
| `oa` | Over-aspirate volume. | `000` to `999`; see `av` |
| `lm` | Height mode: `0` is the fixed height `zl`; `1` to `4` detect the liquid or the well bottom and are not allowed. | `0` only (rule 3) |
| `ll` | Capacitive level detection sensitivity. | Fixed `ll1` |
| `lv` | Pressure level detection sensitivity. | Fixed `lv1` |
| `zo` | Height above the well bottom with `lm4`; unused with `lm0`. | `000` to `999` |
| `ld` | Allowed difference between the two detection methods. | Fixed `ld00` |
| `de` | Speed of leaving the liquid. | Fixed `de0010` |
| `wt` | Settling time after aspirating. | `00` to `99` |
| `mv` | Mix volume (plunger); `0` exactly when `mc` is `0`. | `00000` to the tip's `tv` |
| `mc` | Mix cycles; `00` means no mix. | `00` to `20` |
| `mp` | Mix start below `zl`; see "Mixing". | `000` to `999` |
| `ms` | Mix flow. | `0001` to `1000` with 50 µL tips, `2500` with 1000 µL tips (rule 3) |
| `mh` | Mix stroke depth; see "Mixing". | `0000` to `9999` |
| `gi` | Pressure monitoring. | Fixed `gi000` |
| `gj` | Pressure monitoring. | Fixed `gj0` |
| `gk` | Pressure monitoring. | Fixed `gk0` |
| `lk` | Second-section search. | Fixed `lk0` |
| `ik` | Second-section search. | Fixed `ik0000` |
| `sd` | Second-section search. | Fixed `sd0500` |
| `se` | Second-section search. | Fixed `se0500` |
| `sz` | Second-section search. | Fixed `sz0300` |
| `io` | Second-section search. | Fixed `io0000` |
| `il` | Second-section search; optional. | Fixed `il00000` |
| `in` | Second-section search; optional. | Fixed `in0000` |

The tip aspirates at `zl`, which must be at least 0.5 mm below the surface
that remains after the aspiration (rule 4).

### `C0DS`: dispense

```text
C0DSid0005dm2&tm1 0&xp01274&yp2417&zx1880&lp1980&zl1880&po0050&ip0000&it0&fp0000&zu0000&zr00000&th2450te2450dv00540&ds1000&ss1000&rv000&ta000&ba0000&lm0&dj00zo005&ll1&lv1&de0010&wt00&mv00000&mc00&mp000&ms1000&mh0000&gi000&gj0gk0
```

The example dispenses that liquid into well A2, 2.0 mm above its bottom.

| Field | Meaning | Range |
| --- | --- | --- |
| `dm` | Mode: `0` jet, part volume (uses `rv`); `1` jet with blow-out (uses `ba`); `2` surface, part volume; `3` surface with blow-out (uses `ba`); `4` empty the tip. | `0` to `4` |
| `tm` | Active channels. | `0` or `1` per channel |
| `xp` | X of each channel's well. | With `yp`, within 0.3 mm of a well center (rule 4) |
| `yp` | Y of each channel's well. | With `xp`, within 0.3 mm of a well center |
| `zx` | Lowest Z the tip may reach. | Well bottom + 1.0 mm (rule 4) to `3347` |
| `lp` | Search start for liquid level detection; unused with `lm0`. | `1360` to `3347` |
| `zl` | Dispense height. | Well bottom + 1.0 mm (rule 4) to the channel's top |
| `po` | Distance the tip pulls up after dispensing. | `0000` to the top minus `zl` and `fp` |
| `ip` | Immersion after level detection. | Fixed `ip0000` |
| `it` | Direction of `ip`. | Fixed `it0` |
| `fp` | Distance the tip follows the surface up while dispensing. | `0000` to the top minus `zl` and `po` |
| `zu` | Height of a tapered well bottom, for surface following; `0` for a straight well. | `0000` to `9999` |
| `zr` | Width of that tapered section at its bottom over its width at the top, x 10000. | `00000` to `10000` |
| `th` | Traverse height: channels rise to it before moving. | `2450` to the channel's top |
| `te` | Height after the command. | `2450` to the channel's top |
| `dv` | Plunger volume; see "Volumes". | At most what the tip holds (rule 6) |
| `ds` | Flow. | `0001` to `2000` with 50 µL tips, `4000` with 1000 µL tips (rule 3) |
| `ss` | Plunger stop speed. | Fixed `ss1000` |
| `rv` | Stop-back volume, with `dm0`. | `000` to `999` |
| `ta` | Transport air pushed out. | `000` to `999` |
| `ba` | Blow-out air pushed out, with `dm1` and `dm3`. | `0000` to `9999` |
| `lm` | Height mode: `0` is the fixed height `zl`; `1` to `4` detect the liquid or the well bottom and are not allowed. | `0` only (rule 3) |
| `dj` | Side touch. | Fixed `dj00` |
| `zo` | Height above the well bottom with `lm4`; unused with `lm0`. | `000` to `999` |
| `ll` | Capacitive level detection sensitivity. | Fixed `ll1` |
| `lv` | Pressure level detection sensitivity. | Fixed `lv1` |
| `de` | Speed of leaving the liquid. | Fixed `de0010` |
| `wt` | Settling time after dispensing. | `00` to `99` |
| `mv` | Mix volume (plunger); `0` exactly when `mc` is `0`. | `00000` to the tip's `tv` |
| `mc` | Mix cycles; `00` means no mix. | `00` to `20` |
| `mp` | Mix start below `zl`; see "Mixing". | `000` to `999` |
| `ms` | Mix flow. | `0001` to `1000` with 50 µL tips, `2500` with 1000 µL tips (rule 3) |
| `mh` | Mix stroke depth; see "Mixing". | `0000` to `9999` |
| `gi` | Pressure monitoring. | Fixed `gi000` |
| `gj` | Pressure monitoring. | Fixed `gj0` |
| `gk` | Pressure monitoring. | Fixed `gk0` |

The mode changes only the time and how much leaves the tip, not whether the
tip touched the liquid. The grader counts a dispense as a jet only if the tip
end is at least 1.0 mm above the liquid surface after the dispense;
otherwise the tip touched that well's liquid (rule 5). The twin's
`in_liquid` has no margin: a `dispense` event has it when the tip end ends
at or below the surface, a `mix` event when the bottom of the stroke is.

### `C0TR`: eject tips

```text
C0TRid0006xp08000&yp4050&tp1970tz1870th2450te2450tm1 0&ti0
```

| Field | Meaning | Range |
| --- | --- | --- |
| `xp` | X of each channel's eject. | Waste: `x_mm` within 0.5 mm; return: the spot's X |
| `yp` | Y of each channel's eject. | Waste: the channel's `y_mm` within 0.5 mm; return: the spot's Y |
| `tm` | Active channels. | `0` or `1` per channel |
| `tp` | Tip-end Z where the eject stroke starts. | Waste: `top_z_mm` within 0.5 mm; return: as for a pickup |
| `tz` | Tip-end Z where it ends. | Waste: `bottom_z_mm` within 0.5 mm; return: as for a pickup |
| `th` | Traverse height before the eject. | `2450` to the channel's top |
| `te` | Height after the command. | `2450` to the channel's top |
| `ti` | `0` drops the tips; `1` puts them back into the rack spot below. | `0` or `1` |

Discarded tips (`ti0`) go to the waste position in the deck's
`speedrun.waste`. In the public decks that is
`xp08000&yp4050 3782 3514 3246 2978 2710 2442 2175tp1970tz1870`.

A returned tip (`ti1`) must go into an empty rack spot: one your program has
emptied or one the deck lists as `missing`. Its `tp` and `tz` are those of a
pickup from that rack. It stays there, and you may not pick that spot again.

### `C0PP`, `C0PR`: move a plate

The iSWAP grips a plate with `C0PP` and puts it down with `C0PR`. The grader
accepts only the templates in the deck's `speedrun.iswap_templates`, field
for field except `id`. To move a plate from nest A to nest B, send the `pick`
of A's template and then the `place` of B's, each with `id` and your four
digits inserted after the opcode. In `decks/magnet-cleanup.json`, this moves
plate `beads` from `plate_carrier/3` to `magnet/1`:

```text
C0PPid0101xs01679xd0yj3062yd0zj1929zd0gr1th2800te2800gw4go1330gb1233gt20ga1gc0
C0PRid0102xs01680xd0yj4984yd0zj2188zd0th2800te2800gr1go1330ga1gc0
C0PGid0103th2840
```

A `C0PP` must be followed at once by its `C0PR`, and the `C0PR` by a `C0PG`,
so every move is these three lines. The channels must carry no tips when the
iSWAP grips.

### `C0PG`: park the iSWAP

```text
C0PGid0007th2840
```

`th` is the traverse height while parking.

## Volumes

`av`, `dv` and `mv` are plunger volumes, not liquid volumes. The deck's
`correction_curves` give `[liquid, plunger]` points for each tip capacity;
between points the map is linear. To move 50 µL with 50 µL tips, whose curve
contains `[50.0, 54.0]`, command `av00540` and `dv00540`. Use the curve
whose `tip_capacity_microliters` matches the tip (60.0 for 50 µL tips, 1250.0
for 1000 µL tips).

The grader checks liquid volumes, so get this right.

The grader also checks the plunger (rule 6). From pickup to eject or
return, a tip holds the sum of its `av` minus the sum of its `dv`, and no
dispense may take that below -0.1 µL. A `dm4` dispense may use any `dv` up
to what the tip holds and leaves it at 0. The map is not linear, so several
dispenses from one aspiration take more liquid than they deliver: twelve 30
µL jets from 1000 µL tips need `dv00355` each, so aspirate `av04260` (407 µL
of liquid). Then empty the 47 µL left over back into the source well with
`dm4` and `dv00000`; it counts as never removed.

## Mixing

`C0AS` mixes before it aspirates and `C0DS` mixes after it dispenses. The
tip first goes down `mp` from its height, but not below `zx`: that is the top
of the mix stroke. Each cycle it goes down `mh` more (again not below `zx`)
and back while the plunger draws `mv` and pushes it out. Mix time is about 2
x volume x cycles / flow.

The height is `zl` for `C0AS`. For `C0DS` it is `zl` raised by the distance
the tip follows the liquid up: `fp`, or a different distance inside a
tapered bottom (`zu`, `zr`).

A mix counts for a protocol `mix` step only if the tip stays in the liquid
for the whole stroke (rule 7): the top of the stroke must be at least 0.5 mm
below the surface that is left while the mix volume is in the tip. That
surface is the well bottom plus the liquid height of the well's volume at
the mix minus the mix volume. `mc` is at most 20 on every channel, and a
program has at most 2000 mix cycles in total over all active channels.

## Timing in the twin

Each line in the trace has `ms`, the modeled machine time. Motion follows the
robot's speed and acceleration limits; aspirate and dispense take volume /
flow plus air and settling time. The score adds 0.8 s per command on top.
Read the trace to see where your seconds go.
