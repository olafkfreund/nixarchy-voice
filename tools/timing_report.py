"""n, p50 and p95 per phase, from the TIMING lines in session.log (#79).

    python3 tools/timing_report.py [--since YYYY-MM-DD] [--until YYYY-MM-DD]

The lines are only there if `trace_timings` is on, and it is off by default.
The measurement period is the user turning it on. `--since` is inclusive and
`--until` is not, so `--since A --until B` and `--since B` are two windows
that share no day: a build before B, and a build from B on.

`synth` is the wait before her first sample, by contract (#135): the SYNTH
spans stop there, whether the cloud clip is streamed or fetched whole, so it
compares across that change. `first-audio` is the trace's start to that first
sample. `play` is derived as `speak - synth`: from her first sample to the
mouth returning. The breakdown names which build a window came from:
`synth.download` and `synth.decode` before #135, `synth.buffer` after.

Model time before and after #79 is not like for like: speaking used to be
counted inside `model-turn` and now is `speak`, so `model-turn` falls and no
model got faster.

End of turn (#80). `endpoint` is grouped by the configured `hold`, with its
overshoot `endpoint - hold` (a large one points at the recorder's teardown,
not the hold). A capture cut by the cap while the user was still talking is
the only thing that measures below its hold: those are counted as `under
hold`, not put in the percentiles. The `heard` section counts how many heard
lines end on a fragment (see `is_fragment`); it never prints what was heard.

Deciding the hold, in blocks of at least 50 heard lines, `--since` the day
the hold was last changed:
  - At 0.6 s (the default since #138), 2 or fewer fragment endings in 50
    is acceptable; 4 or more means 0.6 s cuts the user off.
  - If 0.6 s cuts off (4 or more in 50), set `end_of_speech_seconds = 0.8`
    in config.toml and file the revert of the default.
  - 4 or more in 50, at any hold of 0.8 s or less, is the trigger for the
    learned end-of-turn follow-up (smart-turn v3, or holding on after a
    trailing fragment).
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import config  # noqa: E402
from omarchy_voice.trace import _percentile, parse_line  # noqa: E402

# A sentence that stops on one of these was probably cut off. A lower bound:
# a clean-looking half-sentence is not caught.
FRAGMENT_ENDS = (",", "...", "…", "-", "—")
FRAGMENT_WORDS = {"and", "or", "but", "the", "a", "an", "to", "of", "so",
                  "then", "with", "because", "if"}
HEARD = "  heard   "


def is_fragment(text: str) -> bool:
    text = text.strip()
    if text.endswith(FRAGMENT_ENDS):
        return True
    words = text.lower().split()
    return bool(words) and words[-1] in FRAGMENT_WORDS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default="", metavar="YYYY-MM-DD",
                        help="only lines stamped on or after this day")
    parser.add_argument("--until", default="", metavar="YYYY-MM-DD",
                        help="only lines stamped before this day")
    args = parser.parse_args()
    samples: dict[str, list[float]] = {}
    # hold -> (endpoints, overshoots, under hold)
    endpoints: dict[str, tuple[list[float], list[float], list[float]]] = {}
    heard = fragments = 0
    try:
        lines = config.LOG_FILE.read_text().splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        # The stamp is "YYYY-MM-DD HH:MM:SS", so the day compares as text.
        if line[:10] < args.since:
            continue
        if args.until and line[:10] >= args.until:
            continue
        if HEARD in line:
            try:
                text = ast.literal_eval(line.split(HEARD, 1)[1])
            except (ValueError, SyntaxError):
                continue
            if isinstance(text, str):
                heard += 1
                fragments += is_fragment(text)
            continue
        if (parsed := parse_line(line)) is None:
            continue
        hold = parsed.pop("hold", None)
        if "endpoint" in parsed:
            key = f"{hold:.2f}s" if hold is not None else "?"
            done, over, under = endpoints.setdefault(key, ([], [], []))
            value = parsed["endpoint"]
            if hold is not None and value < hold:
                under.append(value)
            else:
                done.append(value)
                if hold is not None:
                    over.append(value - hold)
        if "speak" in parsed:
            parsed["play"] = parsed["speak"] - parsed.get("synth", 0.0)
        for key, value in parsed.items():
            samples.setdefault(key, []).append(value)
    if not samples and not heard:
        print("no tasks traced")
        return 0
    if samples:
        print(f"{'':24} {'n':>5} {'p50':>8} {'p95':>8}")
        for key, values in sorted(samples.items()):
            print(f"{key:24} {len(values):5} {_percentile(values, 0.5):8.2f}"
                  f" {_percentile(values, 0.95):8.2f}")
    for key, (done, over, under) in sorted(endpoints.items()):
        row = f"endpoint, hold={key}: n {len(done)}"
        if done:
            row += (f"  p50 {_percentile(done, 0.5):.2f}"
                    f"  p95 {_percentile(done, 0.95):.2f}")
        if over:
            row += (f"  overshoot p50 {_percentile(over, 0.5):.2f}"
                    f"  p95 {_percentile(over, 0.95):.2f}")
        print(row + f"  under hold: {len(under)}")
    rate = f" ({100 * fragments / heard:.1f} %)" if heard else ""
    print(f"heard: {heard}, fragment endings: {fragments} of {heard}{rate}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
