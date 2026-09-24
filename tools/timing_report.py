"""n, p50 and p95 per phase, from the TIMING lines in session.log (#79).

    python3 tools/timing_report.py [--since YYYY-MM-DD]

The lines are only there if `trace_timings` is on, and it is off by default.
The measurement period is the user turning it on. `play` is derived as
`speak - synth`: her voice coming out of the speakers once the clip is ready.

Model time before and after #79 is not like for like: speaking used to be
counted inside `model-turn` and now is `speak`, so `model-turn` falls and no
model got faster.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import config  # noqa: E402
from omarchy_voice.trace import _percentile, parse_line  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default="", metavar="YYYY-MM-DD",
                        help="only lines stamped on or after this day")
    args = parser.parse_args()
    samples: dict[str, list[float]] = {}
    try:
        lines = config.LOG_FILE.read_text().splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        # The stamp is "YYYY-MM-DD HH:MM:SS", so the day compares as text.
        if line[:10] < args.since or (parsed := parse_line(line)) is None:
            continue
        if "speak" in parsed:
            parsed["play"] = parsed["speak"] - parsed.get("synth", 0.0)
        for key, value in parsed.items():
            samples.setdefault(key, []).append(value)
    if not samples:
        print("no tasks traced")
        return 0
    print(f"{'':24} {'n':>5} {'p50':>8} {'p95':>8}")
    for key, values in sorted(samples.items()):
        print(f"{key:24} {len(values):5} {_percentile(values, 0.5):8.2f}"
              f" {_percentile(values, 0.95):8.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
