"""#26: what a task's time was spent on, and what the trace must never carry."""
import sys
import unittest
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import elevenlabs, feedback
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

    def test_a_tool_between_sentences_is_one_continuation(self):
        """A TURN span per sentence is not a round trip; the tool is (#79)."""
        task = trace_mod.Trace()
        for phase in (trace_mod.TURN, trace_mod.TURN, trace_mod.TOOL,
                      trace_mod.TURN):
            task.mark(phase).close()
        self.assertEqual(task.finish().continuations, 1)

    def test_parse_line_reads_what_line_writes(self):
        speak, synth = "speak", "synth"  # trace.SPEAK / SYNTH (#79)
        task = trace_mod.Trace(started=0.0, ended=2.0)
        task.spans = [trace_mod.Span(*span) for span in (
            (trace_mod.TURN, "", 0.0, 0.5),
            (trace_mod.SUBPROCESS, "grim", 0.1, 0.2),
            (speak, "", 0.5, 2.0),
            (synth, "request", 0.5, 0.6),
            (synth, "download", 0.6, 0.8),
            (synth, "decode", 0.8, 0.9))]
        parsed = trace_mod.parse_line("2026-09-24 12:00:00  " + task.line())
        expected = {"total": 2.0, "continuations": 0, "first-audio": 0.9,
                    "speak": 1.5, "synth": 0.4, "synth.request": 0.1,
                    "synth.download": 0.2, "synth.decode": 0.1,
                    "subprocess": 0.1, "subprocess.grim": 0.1,
                    "model-turn": 0.5}
        self.assertEqual(set(parsed), set(expected))
        for key, value in expected.items():
            self.assertAlmostEqual(parsed[key], value, places=2, msg=key)
        self.assertIsNone(trace_mod.parse_line("heard   'x'"))


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

    def test_speech_spans_carry_fixed_names_only(self):
        """Her words reach the mouth, and never the trace (#79)."""
        mouth = feedback.Feedback(Config(
            elevenlabs_enabled=True, elevenlabs_voice_id="abc", speak=True))
        mouth.log = lambda line: None
        mouth.trace = task = trace_mod.Trace()
        response = mock.MagicMock()
        response.read.return_value = b"ID3fake-mp3"
        response.__enter__.return_value = response
        done = mock.MagicMock(returncode=0, stdout=b"pcm")
        with mock.patch.object(elevenlabs, "ready", return_value=True), \
                mock.patch.object(elevenlabs, "api_key", return_value="k"), \
                mock.patch("shutil.which", return_value="/bin/ffmpeg"), \
                mock.patch("urllib.request.urlopen", return_value=response), \
                mock.patch("subprocess.run", return_value=done), \
                mock.patch.object(feedback.Feedback, "_play"):
            mouth._speak_now(self.SECRETS[0])
        spoken = [s for s in task.spans if s.phase in ("speak", "synth")]
        self.assertTrue([s for s in spoken if s.phase == "synth"],
                        "no synthesis span was recorded")
        for span in spoken:
            self.assertIn(span.name, {"", "request", "download", "decode"})
        self.assertNotIn(self.SECRETS[0], task.finish().line())

    def test_the_flag_is_off_by_default(self):
        self.assertFalse(Config().trace_timings)


class SubprocessPhase(unittest.TestCase):
    """#39: the phase the trace declared and never recorded."""

    def executor(self):
        ex = Executor(Config(dry_run=False))
        ex.trace = trace_mod.Trace(label="test")
        return ex

    def test_the_span_carries_the_program_name_and_not_the_arguments(self):
        ex = self.executor()
        ex._shell(["echo", "a-secret-the-user-said"], timeout=5)
        names = [s.name for s in ex.trace.spans if s.phase == trace_mod.SUBPROCESS]
        self.assertEqual(names, ["echo"])
        self.assertNotIn("secret", " ".join(names))

    def test_a_grace_return_closes_the_span_at_return_not_at_child_exit(self):
        ex = self.executor()
        result = ex._shell(["sleep", "10"], timeout=30, grace=0.2)
        self.assertEqual(result.output, "started")
        span = next(s for s in ex.trace.spans if s.name == "sleep")
        self.assertIsNotNone(span.ended)
        self.assertLess(span.seconds, 5)  # not the child's ten

    def test_a_missing_binary_still_closes_its_span(self):
        ex = self.executor()
        ex._shell(["this-binary-does-not-exist"], timeout=5)
        span = next(s for s in ex.trace.spans if s.phase == trace_mod.SUBPROCESS)
        self.assertIsNotNone(span.ended)

    def test_nothing_is_recorded_without_a_trace(self):
        ex = Executor(Config(dry_run=False))
        self.assertIsNone(ex.trace)
        self.assertTrue(ex._shell(["echo", "hi"], timeout=5).ok)

    def test_the_phase_is_aggregated_by_program(self):
        t = trace_mod.Trace()
        for name, held in (("hyprctl", 0.2), ("hyprctl", 0.1), ("omarchy", 0.5)):
            span = t.mark(trace_mod.SUBPROCESS, name)
            span.span.ended = span.span.started + held
        totals = t.subprocess_seconds()
        self.assertAlmostEqual(totals["hyprctl"], 0.3, places=2)
        self.assertAlmostEqual(totals["omarchy"], 0.5, places=2)

    def test_only_this_phase_is_broken_down_in_the_line(self):
        t = trace_mod.Trace()
        for phase, name, held in ((trace_mod.SUBPROCESS, "hyprctl", 0.2),
                                  (trace_mod.OCR, "", 1.9)):
            span = t.mark(phase, name)
            span.span.ended = span.span.started + held
        line = t.finish().line()
        self.assertIn("subprocess=0.20s(hyprctl=0.20)", line)
        self.assertIn("ocr=1.90s", line)
        self.assertNotIn("ocr=1.90s(", line)

if __name__ == "__main__":
    unittest.main()
