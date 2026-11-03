from __future__ import annotations

import json
import os
import platform
import subprocess

from .protocol import HAMILTON

SYSTEMS = {"Darwin": "macos", "Linux": "linux"}
MACHINES = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64", "AMD64": "x86_64"}


def binary():
    if os.environ.get("SPEEDRUN_TWIN"):
        return os.environ["SPEEDRUN_TWIN"]
    system, machine = platform.system(), platform.machine()
    path = HAMILTON / "bin" / f"twin-{SYSTEMS.get(system, system)}-{MACHINES.get(machine, machine)}"
    if path.exists():
        return str(path)
    raise RuntimeError(f"no twin binary: expected bin/{path.name} or $SPEEDRUN_TWIN")


def run(scenario_path, program, timeout=300):
    try:
        result = subprocess.run(
            [binary(), str(scenario_path)],
            input=program,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"the twin did not finish within {timeout} s") from None
    except OSError as error:
        raise RuntimeError(f"cannot run the twin: {error.strerror}") from None
    if result.returncode:
        raise RuntimeError(f"twin failed: {result.stderr.strip()[-500:]}")
    return [json.loads(line) for line in result.stdout.split("\n") if line]
