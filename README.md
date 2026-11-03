# C5R student kit

Two problems from our lab, each with a local scorer. Pick one or do both.

| Project | You write | Score (lower is better) |
| --- | --- | --- |
| [Hamilton Speedrun](hamilton/README.md) | A firmware program that runs a pipetting protocol on a Hamilton STARlet liquid handler | Machine seconds |
| [Facility Planning](facility/README.md) | A layout that places lab equipment in a room | Minutes to finish one day of runs |

The scorer in this kit is the same code the leaderboard runs, so the score you
see locally is the score you get when you submit.

Each project comes with a simple solver. It always produces a valid answer,
and it is slow. Your job is to beat it by a wide margin.

## Requirements

- Python 3.9 or newer, standard library only. The `python3` that comes with
  macOS works. On a Mac that has never run it, the first `python3` command
  offers a one-time download of the Command Line Tools, which takes several
  minutes and needs the network. Run `python3 --version` before the event and
  accept the download.
- macOS (Apple silicon or Intel) or Linux (x86_64 or arm64). On Windows, use
  WSL with Ubuntu: unzip the kit with `unzip` inside the WSL shell
  (`sudo apt install unzip` if it is missing), run everything there, and open
  the viewers from Windows with `explorer.exe .`.
- Apart from that download, the kit installs nothing and runs offline.

## Setup

Unzip the kit and open a terminal in its folder, for example:

```sh
cd ~/Downloads/c5r-stanford-kit
xattr -dr com.apple.quarantine .     # macOS only: lets the Hamilton twin run
```

## Quickstart: Hamilton Speedrun

```sh
cd hamilton
python3 -m speedrun solve protocols/serial-dilution.json > program.txt
python3 -m speedrun score protocols/serial-dilution.json program.txt
```

`solve` writes the reference program. `score` replays it on the robot's
digital twin, which takes about 25 s and prints nothing until it is done:

```text
valid: 3162.7 s (2669.1 s robot time + 617 commands x 0.8 s)
trace: program.trace.jsonl
```

To watch the run, save the trace and a report and open the viewer:

```sh
python3 -m speedrun score protocols/serial-dilution.json program.txt --trace trace.jsonl --json > report.json
open viewer/hamilton.html        # Linux: xdg-open
```

In the viewer, add `decks/serial-dilution.json`, `trace.jsonl` and
`report.json`. Choose them one at a time, or drag all three onto the page.

Then read [hamilton/README.md](hamilton/README.md).

To write your own solver, start from the starter solver (`python3 -m speedrun
starter PROTOCOL`) and the `Robot` class: see
[Write a solver](hamilton/README.md#write-a-solver).

## Quickstart: Facility Planning

```sh
cd ../facility       # from the kit folder: cd facility
python3 -m facility solve floorplans/h0.json > layout.json
python3 -m facility score floorplans/h0.json layout.json
```

`solve` writes the baseline layout. `score` checks it against the safety
rules, simulates the floor plan's runs file (one day of runs) and prints the
minutes it took (292.228 for this baseline), then when each run finished.

To see the layout, save a report and open the viewer:

```sh
python3 -m facility score floorplans/h0.json layout.json --json > report.json
open viewer/facility.html        # Linux: xdg-open
```

In the viewer, add `floorplans/h0.json`, `catalog.json`, `layout.json` and
`report.json`. Choose them one at a time, or drag all four onto the page.
Then read [facility/README.md](facility/README.md).

## Submit

1. Open https://stanford-bb1494.us-west-1.aws.c5r.net, go to **Submit** and
   create a team. Your team token is shown once. The page remembers it in
   that browser; send it to your teammates so they can join the team.
2. On the project page, choose the protocol or floor plan, add your
   `program.txt` or `layout.json` (or paste it) and press **Score**. You get
   the same report as the local scorer and a page for that run. A Hamilton
   program takes about as long to score on the site as locally, plus any wait
   in the queue; the site stops scoring after 300 s.
3. Each protocol and floor plan has its own leaderboard, ranked by each
   team's best valid score; ties go to the earlier submission. The overall
   table ranks teams by how many instances they solved, then by the sum of
   their ranks. An instance you have not solved counts as the number of
   teams in the table plus one, so cover every one.

Practice run pages are public: anyone can step through every command of a
Hamilton run, with its positions and channels, or see a layout's drawing
with every item where it stands. Only your team can download the file you
submitted. Replays and files are kept only for each team's best and latest
runs, up to a storage limit. The final stays hidden until it closes, and the
final decides the winner.

Limits per team: 2 submissions scoring at a time, 30 submissions per 10
minutes, 1 MB per program or layout.

## The final

Near the end of the evening we release one new instance per project on the
site: a new protocol on the same carriers (its plates, tubes and tip racks can
differ, so read them from its deck), and a new floor plan with its own runs file. A **Final** box with a download button and the deadline
appears on the home page and on the project page; the page updates on its
own.

1. Download the instance and unzip it in your kit folder, so its files land
   next to the public ones:

   ```sh
   cd ~/Downloads/c5r-stanford-kit
   unzip -o ~/Downloads/NAME.zip
   ```

   If your browser unpacked the download already, move its files into the
   same folders of the kit: the protocol into `hamilton/protocols/` and its
   deck into `hamilton/decks/`, or the floor plan into `facility/floorplans/`
   and its runs file into `facility/runs/`.
2. Run your solver on it and check the answer with the local scorer.
3. Submit the answer on the project page before the deadline. The final
   appears first in the list, marked **Final**.

The final has its own leaderboard, ranked by each team's best valid
submission before the deadline; later submissions are refused. It decides the
winner and does not count toward the overall table. Until the deadline, its
run pages show only the score and failures, with no replay or drawing. The
fastest valid Hamilton programs then run on our real Hamilton STARlet.

Have your solver ready before the release: it should take a protocol or floor
plan it has never seen and produce a valid answer within minutes. Test it on
every public instance first.

You may also upload the solver you used, as a `.zip` up to 1 MB, on the
project page. We keep it with your team's entry and never run it.
