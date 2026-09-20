"""Where a turn's time went, in phases, with nothing in it but phases.

Every latency claim about this program so far has been a component benchmark:
grim takes so long, tesseract takes so long, the brain takes so long. None of
those is what the user waits for. A task is model turns and tool calls
interleaved, and the interesting question -- whether a change removed work or
just moved it -- is only answerable from the whole thing.

Two consumers, one recorder:

  tools/bench_local.py   runs a fixed task list and reports the distribution
  session.log            one line per task in real use, behind a flag, off by
                         default

The second exists because a bench only measures the tasks somebody thought to
script, and the slow turns that matter are the ones nobody predicted.

**Nothing here records content.** `mark` takes a phase name and that is all it
takes -- there is no parameter a window title, a line of OCR or a tool argument
could arrive through. That is deliberate, and it is stronger than filtering
them out on the way to the log: a filter is a thing that can have a bug, and
the value here is entirely in the durations anyway. The one piece of caller
data is the tool's *name*, which is a fixed vocabulary from TOOL_SCHEMAS.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# A model was called again because it was waiting on a tool result. This is the
# number a coarser tool would move, and it is not the number of tool calls: a
# model can ask for three tools in one response and pay one round trip, so
# counting calls would make any composite-tool argument unfalsifiable by
# flattering it.
TURN = "model-turn"
TOOL = "tool"
LOCK = "lock"
CAPTURE = "capture"
OCR = "ocr"
SUBPROCESS = "subprocess"


@dataclass
class Span:
    phase: str
    name: str
    started: float
    ended: float | None = None

    @property
    def seconds(self) -> float:
        return (self.ended if self.ended is not None else time.monotonic()) - self.started


@dataclass
class Trace:
    """One task, from the end of the utterance to the verified result."""

    label: str = ""
    started: float = field(default_factory=time.monotonic)
    spans: list[Span] = field(default_factory=list)
    ended: float | None = None
    # Set by whoever can tell. None means nobody checked, which is not the same
    # as "it went to the right place" and is reported separately.
    hit_target: bool | None = None

    def mark(self, phase: str, name: str = ""):
        """Open a span. Use as a context manager, or call .close() yourself."""
        span = Span(phase, name, time.monotonic())
        self.spans.append(span)
        return _Open(span)

    def finish(self) -> Trace:
        self.ended = time.monotonic()
        return self

    @property
    def seconds(self) -> float:
        return (self.ended if self.ended is not None else time.monotonic()) - self.started

    @property
    def continuations(self) -> int:
        """Model turns after the first: the round trips a tool result cost.

        A task that is one question and one answer has none. Each additional
        model turn is one the user waited through because a tool had to run
        first.
        """
        return max(0, sum(1 for s in self.spans if s.phase == TURN) - 1)

    def phase_seconds(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for span in self.spans:
            totals[span.phase] = totals.get(span.phase, 0.0) + span.seconds
        return totals

    def line(self) -> str:
        """The session.log line. Phase names and durations, nothing else."""
        parts = " ".join(f"{phase}={seconds:.2f}s"
                         for phase, seconds in sorted(self.phase_seconds().items()))
        return (f"TIMING  {self.seconds:.2f}s "
                f"continuations={self.continuations} {parts}".rstrip())


class _Open:
    def __init__(self, span: Span):
        self.span = span

    def close(self) -> None:
        if self.span.ended is None:
            self.span.ended = time.monotonic()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank, because these samples are counted in tens, not thousands.

    Interpolating between two of eleven runs invents a number that no run
    produced; the nearest rank is at least a thing that happened.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * len(ordered) + 0.5) - 1))
    return ordered[index]


def summarise(traces: list[Trace]) -> dict:
    """The three numbers a change to this program is judged on."""
    done = [t for t in traces if t.seconds > 0]
    checked = [t for t in done if t.hit_target is not None]
    return {
        "tasks": len(done),
        "p50": _percentile([t.seconds for t in done], 0.50),
        "p95": _percentile([t.seconds for t in done], 0.95),
        "continuations_p50": _percentile(
            [float(t.continuations) for t in done], 0.50),
        # Separate from "all of them passed": nobody checking is not a pass.
        "target_checked": len(checked),
        "wrong_target": sum(1 for t in checked if t.hit_target is False),
        "phases": {
            phase: round(sum(t.phase_seconds().get(phase, 0.0) for t in done), 2)
            for phase in sorted({p for t in done for p in t.phase_seconds()})
        },
    }


def report(traces: list[Trace]) -> str:
    data = summarise(traces)
    if not data["tasks"]:
        return "no tasks traced"
    unchecked = data["tasks"] - data["target_checked"]
    lines = [
        f"{data['tasks']} tasks   p50 {data['p50']:.2f}s   p95 {data['p95']:.2f}s",
        f"dependent model continuations, p50: {data['continuations_p50']:.0f}",
        (f"wrong target: {data['wrong_target']} of {data['target_checked']} checked"
         + (f" ({unchecked} unchecked)" if unchecked else "")),
        "time by phase: " + ", ".join(f"{p} {s}s" for p, s in data["phases"].items()),
    ]
    return "\n".join(lines)
