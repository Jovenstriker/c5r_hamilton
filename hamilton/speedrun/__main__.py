from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import grade, protocol

MAX_FAILURES = 20


def describe(error):
    if isinstance(error, UnicodeDecodeError):
        return "the program is not UTF-8 text"
    if isinstance(error, OSError):
        return f"cannot open {error.filename}: {error.strerror}"
    return str(error)


def capped(failures):
    if len(failures) > MAX_FAILURES:
        return failures[:MAX_FAILURES] + [f"... and {len(failures) - MAX_FAILURES} more"]
    return failures


def solve(args):
    from . import reference, starter

    try:
        loaded = protocol.load(args.protocol)
        if args.command == "starter":
            program = starter.solve(loaded, loaded["deck"])
        else:
            program = "".join(wire + "\n" for wire in reference.solve(loaded, loaded["deck"]))
    except (OSError, ValueError) as error:
        print(f"error: {describe(error)}", file=sys.stderr)
        return 1
    print(program, end="")
    return 0


def score(args):
    program = Path(args.program)
    trace_path = Path(args.trace) if args.trace else program.with_suffix(".trace.jsonl")
    name, written = None, None
    try:
        trace_path.write_text("")
        loaded = protocol.load(args.protocol)
        name = loaded["id"]
        report, trace = grade.score(loaded, program.read_bytes().decode("utf-8-sig"))
        trace_path.write_text("".join(json.dumps(line) + "\n" for line in trace))
        written = str(trace_path) if trace else None
    except (OSError, ValueError) as error:
        report = {"valid": False, "seconds": None, "twin_seconds": None, "commands": 0, "failures": [describe(error)]}
    problems = len(report["failures"])
    report = {"protocol": name, **report, "failures": capped(report["failures"]), "trace": written}
    print(json.dumps(report) if args.json else text_report(report, problems))
    return 0 if report["valid"] else 1


def text_report(report, problems):
    if report["valid"]:
        head = (
            f"valid: {report['seconds']:.1f} s ({report['twin_seconds']:.1f} s robot time + "
            f"{report['commands']} commands x {grade.LATENCY_S} s)"
        )
    else:
        head = "\n  ".join([f"invalid: {problems} problem(s)", *report["failures"]])
    return f"{head}\ntrace: {report['trace']}" if report["trace"] else head


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m speedrun")
    commands = parser.add_subparsers(dest="command", required=True)
    solver = commands.add_parser("solve", help="print the reference program for a protocol")
    solver.add_argument("protocol")
    commands.add_parser("starter", help="print the starter solver's program for a protocol").add_argument("protocol")
    scorer = commands.add_parser("score", help="replay a program on the twin and grade it")
    scorer.add_argument("protocol")
    scorer.add_argument("program")
    scorer.add_argument("--trace", help="where to write the twin trace (default: PROGRAM.trace.jsonl)")
    scorer.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    return score(args) if args.command == "score" else solve(args)


if __name__ == "__main__":
    sys.exit(main())
