#!/usr/bin/env python3
"""Measure the desktop loop against a throwaway VM, not the user's session.

Every measurement in the #26/#27/#28 work ran against the real desktop: probe
windows spawned, a throwaway browser launched, the pointer moved, and whatever
happened to be on screen OCR'd. That is disruptive, and it is not reproducible
-- full-screen OCR measured 1.1s to 5.3s in one session on screen CONTENT
alone. A figure of 3.97s was published in an intent and had to be corrected.

This runs the same checks against a guest with a known set of windows, so two
runs are comparable. It does NOT boot or stop anything: give it a machine that
is already running, and if nothing answers it says how to start one.

    QEMU_OPTS="-display none -serial mon:stdio" nix run github:olafkfreund/nixarchy#vm
    nix develop -c python3 tools/live_check.py

Nothing is copied into the guest. The VM shares the host's /nix/store over
virtiofs, so a path from `nix build .#omarchy-voice` runs directly inside --
which matters, because that closure is 6.7 GiB and the guest discards its root
on every boot.

What it reports is #39's trace phases over whole tasks, with the SPREAD as well
as the middle. #23 requires changes be argued from task completion rather than a
stopwatch on one component, and hiding a wide spread behind an average is how
3.97s got published.

**These numbers describe the guest.** Different applications, no accessibility
(nixarchy#823), a different window set. They are comparable to each other and
not to the machine you are sitting at.
"""
from __future__ import annotations

import argparse
import re
import shutil
import statistics
import time
import subprocess
import sys

LAUNCH = ('QEMU_OPTS="-display none -serial mon:stdio" '
          'nix run github:olafkfreund/nixarchy#vm')

# Fixed, so two runs see the same screen. The point of the whole exercise.
LAYOUT = (
    'foot --app-id=live-check-a -e sh -c "printf \'LIVE CHECK PANE A\\n\'; sleep 9000"',
    'foot --app-id=live-check-b -e sh -c "printf \'LIVE CHECK PANE B\\n\'; sleep 9000"',
)

TASKS = (
    "read the screen and tell me the first heading you can see",
    "read the screen and list any window titles you can make out",
)


# hyprctl needs the instance signature as well as the socket: without it a
# non-session shell gets an empty client list and everything downstream looks
# like an empty desktop rather than a missing variable.
GUEST_ENV = ("export XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-1 "
             "HYPRLAND_INSTANCE_SIGNATURE=$(ls /run/user/1000/hypr 2>/dev/null | head -1); ")


def _ssh(target: str, port: int, password: str, command: str,
         timeout: float = 300.0) -> subprocess.CompletedProcess:
    """One ssh call. Password auth because the guest offers nothing else."""
    argv = ["sshpass", "-p", password, "ssh", "-p", str(port),
            "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=10", target, command]
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def reachable(target: str, port: int, password: str) -> bool:
    try:
        return _ssh(target, port, password, "true", timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def build_package() -> str | None:
    """The store path to exercise. Shared into the guest, never copied."""
    try:
        done = subprocess.run(["nix", "build", ".#omarchy-voice", "--no-link",
                               "--print-out-paths"], capture_output=True,
                              text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip().splitlines()[-1] if done.returncode == 0 and done.stdout.strip() else None


def lay_out(target: str, port: int, password: str) -> list[str]:
    """Launch the fixed window set and report what the guest ended up with."""
    # A guest left alone reaches its screensaver, which covers the layout and
    # makes every capture a picture of the screensaver. Found the hard way:
    # OCR of the guest returned nothing until these were stopped.
    _ssh(target, port, password,
         "pkill -f omarchy-screensaver; pkill -f hypridle; true", timeout=20)
    for command in LAYOUT:
        _ssh(target, port, password, f"{GUEST_ENV} setsid {command} >/dev/null 2>&1 &",
             timeout=30)
    time.sleep(3)  # let them map before asking what is on screen
    listing = _ssh(target, port, password,
                   GUEST_ENV + "hyprctl -j clients | grep -o '\"class\": \"[^\"]*\"' "
                   "| cut -d'\"' -f4 | sort -u", timeout=30)
    return [line.strip() for line in listing.stdout.splitlines() if line.strip()]


def run_task(target: str, port: int, password: str, package: str, task: str,
             brain_url: str, model: str) -> str | None:
    """One task with the trace on, in a config the guest does not keep."""
    # The guest has no credentials and should not get any. QEMU's user-net
    # gateway reaches the host, where ollama listens on *:11434 -- so the
    # planner runs locally, costs nothing, and no secret enters the VM.
    config = ("[hands]\\ntrace_timings = true\\n[mouth]\\nspeak = false\\n"
              f"[openai]\\nbase_url = \\\"{brain_url}\\\"\\nplanner_model = \\\"{model}\\\"\\n"
              "[realtime]\\nenabled = false\\n")
    setup = (GUEST_ENV + "d=$(mktemp -d); mkdir -p $d/omarchy-voice; "
             f"printf '{config}' > $d/omarchy-voice/config.toml; "
             "export XDG_CONFIG_HOME=$d XDG_STATE_HOME=$d OPENAI_API_KEY=local; ")
    done = _ssh(target, port, password,
                f'{setup} {package}/bin/omarchy-voice say {task!r} 2>&1')
    for line in done.stdout.splitlines():
        if "TIMING" in line:
            return line.strip()
    return None


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def phases(line: str) -> dict[str, float]:
    """Parse a TIMING line. The CLI wraps it in ANSI dim codes, and an
    unstripped escape makes the last phase silently unparseable."""
    line = ANSI.sub("", line)
    out = {}
    for token in line.split():
        if "=" in token and token.endswith("s"):
            name, _, value = token.partition("=")
            try:
                out[name] = float(value.split("(")[0].rstrip("s"))
            except ValueError:
                continue
    return out


def report(rows: list[dict[str, float]]) -> None:
    names = sorted({k for row in rows for k in row})
    if not names:
        print("  no timings collected -- the guest ran the tasks but emitted no "
              "phases. Check that trace_timings reached it.")
        return
    width = max(len(n) for n in names)
    print(f"\n  {'phase':<{width}}  {'median':>8}  {'range':>16}   n")
    for name in names:
        values = [row[name] for row in rows if name in row]
        low, high = min(values), max(values)
        print(f"  {name:<{width}}  {statistics.median(values):7.2f}s  "
              f"{low:6.2f}s - {high:6.2f}s  {len(values)}")
    print("\n  The range is the finding when it is wide. These numbers describe the")
    print("  guest -- different applications, no accessibility, a different window")
    print("  set -- so they compare to each other and not to your own desktop.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", default="omarchy@localhost")
    parser.add_argument("--port", type=int, default=2222)
    parser.add_argument("--password", default="omarchy")
    parser.add_argument("--package", default=None,
                        help="store path to exercise; default builds .#omarchy-voice")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--brain-url", default="http://10.0.2.2:11434/v1",
                        help="the host's ollama, over QEMU's user-net gateway")
    parser.add_argument("--model", default="qwen2.5:7b")
    args = parser.parse_args()

    if not shutil.which("sshpass"):
        print("  sshpass is missing. Run this from `nix develop`.", file=sys.stderr)
        return 2

    if not reachable(args.target, args.port, args.password):
        print(f"  nothing answered at {args.target}:{args.port}.", file=sys.stderr)
        print("  This measures a throwaway VM, never the desktop you are on. Start one:",
              file=sys.stderr)
        print(f"\n    {LAUNCH}\n", file=sys.stderr)
        return 1

    package = args.package or build_package()
    if not package:
        print("  could not build .#omarchy-voice", file=sys.stderr)
        return 2
    print(f"  guest    {args.target}:{args.port}")
    print(f"  package  {package}")

    windows = lay_out(args.target, args.port, args.password)
    print(f"  layout   {len(windows)} window classes: {', '.join(windows) or '(none seen)'}")

    rows = []
    for _ in range(args.repeat):
        for task in TASKS:
            line = run_task(args.target, args.port, args.password, package, task,
                            args.brain_url, args.model)
            if line:
                rows.append(phases(line))
    report(rows)
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
