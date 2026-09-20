"""#26: what a task's time was spent on, and what the trace must never carry."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import trace as trace_mod
from omarchy_voice.config import Config
from omarchy_voice.tools import Executor, Result


class ContinuationTests(unittest.TestCase):
    """The metric the composite-tool argument in #23 will be judged on.

    A dependent continuation is a model round trip that happened because a tool
    result was awaited. Counting *tool calls* instead would flatter any coarser
    tool automatically -- three calls become one, the number falls, and nothing
    was actually learnt. A model can ask for three tools in one response and
    pay one round trip; that is one continuation, not three.
    """

    def test_a_task_with_no_tools_has_no_continuations(self):
        task = trace_mod.Trace()
        task.mark(trace_mod.TURN).close()
        self.assertEqual(task.finish().continuations, 0)

    def test_three_tools_in_one_response_are_one_continuation(self):
        task = trace_mod.Trace()
        first = task.mark(trace_mod.TURN)
        for name in ("hypr_query", "read_screen", "click_text"):
            task.mark(trace_mod.TOOL, name).close()
        first.close()
        # The model came back once, after all three.
        task.mark(trace_mod.TURN).close()
        self.assertEqual(task.finish().continuations, 1)

    def test_three_sequential_round_trips_are_three(self):
        task = trace_mod.Trace()
        for _ in range(4):                      # first turn plus three returns
            turn = task.mark(trace_mod.TURN)
            task.mark(trace_mod.TOOL, "hypr_query").close()
            turn.close()
        self.assertEqual(task.finish().continuations, 3)


class SummaryTests(unittest.TestCase):
    def _task(self, seconds: float, hit=None) -> trace_mod.Trace:
        task = trace_mod.Trace()
        task.started = 0.0
        task.ended = seconds
        task.hit_target = hit
        return task

    def test_percentiles_are_values_that_happened(self):
        """Nearest-rank, not interpolated: with eleven runs, an interpolated
        p95 is a number no run produced."""
        data = trace_mod.summarise([self._task(float(n)) for n in range(1, 12)])
        self.assertIn(data["p50"], [float(n) for n in range(1, 12)])
        self.assertIn(data["p95"], [float(n) for n in range(1, 12)])

    def test_nobody_checking_the_target_is_not_a_pass(self):
        data = trace_mod.summarise([self._task(1.0), self._task(1.0)])
        self.assertEqual(data["target_checked"], 0)
        self.assertEqual(data["wrong_target"], 0)
        self.assertIn("unchecked", trace_mod.report([self._task(1.0)]))

    def test_a_wrong_target_is_counted_against_the_checked_ones(self):
        data = trace_mod.summarise(
            [self._task(1.0, hit=True), self._task(1.0, hit=False)])
        self.assertEqual(data["target_checked"], 2)
        self.assertEqual(data["wrong_target"], 1)


class RedactionTests(unittest.TestCase):
    """The flag records durations. It must not become a content log.

    Guaranteed by construction rather than by filtering: `mark` takes a phase
    and a name, and there is no parameter a title, a line of OCR or a tool
    argument could arrive through. A filter is a thing that can have a bug.
    """

    SECRETS = ("Private Bank Statement",      # a window title
               "balance 41,233.18",           # a line of OCR
               "resignation-letter.odt")      # a tool argument

    def _traced_task(self):
        executor = Executor(Config(dry_run=True, trace_timings=True))
        task = trace_mod.Trace()
        executor.trace = task
        with mock.patch.object(Executor, "_ocr_region",
                               lambda self, geometry: Result(True, self.SECRET)), \
             mock.patch.object(Executor, "_target_geometry",
                               lambda self, target="screen": ("0,0 10x10", None)):
            Executor.SECRET = self.SECRETS[1]
            executor.call("read_screen", {"target": self.SECRETS[0],
                                          "query": self.SECRETS[2]})
        return task.finish()

    def test_the_line_carries_durations_and_phase_names_only(self):
        line = self._traced_task().line()
        for secret in self.SECRETS:
            self.assertNotIn(secret, line)
        self.assertIn("TIMING", line)
        self.assertIn("continuations=", line)

    def test_no_span_carries_content_either(self):
        """Not just the rendered line -- the spans themselves.

        A later change that logs the trace differently would otherwise be one
        edit away from leaking, with this test still green.
        """
        task = self._traced_task()
        for span in task.spans:
            for secret in self.SECRETS:
                self.assertNotIn(secret, span.name)
            # Tool names are a fixed vocabulary from TOOL_SCHEMAS. Anything
            # else in this field is caller data, which is how content gets in.
            self.assertLess(len(span.name), 40)

    def test_the_flag_is_off_by_default(self):
        self.assertFalse(Config().trace_timings)


if __name__ == "__main__":
    unittest.main()
