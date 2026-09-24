"""#26: what a task's time was spent on, and what the trace must never carry."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import elevenlabs, feedback
from omarchy_voice import trace as trace_mod
from omarchy_voice.config import Config
from omarchy_voice.tools import Executor, Result

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import timing_report  # noqa: E402
import test_elevenlabs as el_fakes  # noqa: E402  -- the fakes, not its tests


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
        task = trace_mod.Trace(started=0.0, ended=2.0, hold=0.8)
        task.spans = [trace_mod.Span(*span) for span in (
            (trace_mod.TURN, "", 0.0, 0.5),
            (trace_mod.SUBPROCESS, "grim", 0.1, 0.2),
            (speak, "", 0.5, 2.0),
            (synth, "request", 0.5, 0.6),
            (synth, "download", 0.6, 0.8),
            (synth, "decode", 0.8, 0.9))]
        parsed = trace_mod.parse_line("2026-09-24 12:00:00  " + task.line())
        expected = {"total": 2.0, "continuations": 0, "first-audio": 0.9,
                    "hold": 0.8,
                    "speak": 1.5, "synth": 0.4, "synth.request": 0.1,
                    "synth.download": 0.2, "synth.decode": 0.1,
                    "subprocess": 0.1, "subprocess.grim": 0.1,
                    "model-turn": 0.5}
        self.assertEqual(set(parsed), set(expected))
        for key, value in expected.items():
            self.assertAlmostEqual(parsed[key], value, places=2, msg=key)
        self.assertIsNone(trace_mod.parse_line("heard   'x'"))


class SpeechSpanTests(unittest.TestCase):
    """SYNTH spans are the wait before her first sample, before and after
    streaming (#135), so `synth=` and `first-audio=` compare across it."""

    def test_synth_spans_stop_at_the_first_sample(self):
        clock = [100.0]

        def step(seconds):
            return lambda: clock.__setitem__(0, clock[0] + seconds)

        fake_time = mock.MagicMock()
        fake_time.monotonic.side_effect = lambda: clock[0]
        children = el_fakes._Children(on_first_pcm=step(0.1),
                                      on_pw_wait=step(2.0))
        with mock.patch.object(trace_mod, "time", fake_time), \
                el_fakes._harness(el_fakes._Body([b"mp3-1", b"mp3-2"]),
                                  children, on_open=step(0.2)):
            task = trace_mod.Trace(started=clock[0])
            with task.mark(trace_mod.SPEAK):
                elevenlabs.speak("hello", el_fakes._config(), trace=task)
            task.finish()
            line = task.line()
        synth = [s for s in task.spans if s.phase == trace_mod.SYNTH]
        self.assertEqual([s.name for s in synth], ["request", "buffer"])
        self.assertIn("synth=0.30s", line)
        self.assertAlmostEqual(task.first_audio, 0.30, places=6)

        # Comparability: main's names and the branch's names, same wait.
        def shaped(*synth_spans):
            shaped_task = trace_mod.Trace(started=0.0, ended=3.0)
            shaped_task.spans = [trace_mod.Span(trace_mod.SPEAK, "", 0.5, 3.0)] + [
                trace_mod.Span(trace_mod.SYNTH, name, start, end)
                for name, start, end in synth_spans]
            return shaped_task

        before = shaped(("request", 0.5, 0.7), ("download", 0.7, 0.75),
                        ("decode", 0.75, 0.8))
        after = shaped(("request", 0.5, 0.7), ("buffer", 0.7, 0.8))
        self.assertAlmostEqual(before.first_audio, after.first_audio, places=6)
        self.assertIn("synth=0.30s", before.line())
        self.assertIn("synth=0.30s", after.line())


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
        with mock.patch.object(elevenlabs, "ready", return_value=True), \
                el_fakes._harness(el_fakes._Body([b"ID3fake-mp3"])):
            mouth._speak_now(self.SECRETS[0])
        spoken = [s for s in task.spans if s.phase in ("speak", "synth")]
        self.assertTrue([s for s in spoken if s.phase == "synth"],
                        "no synthesis span was recorded")
        for span in spoken:
            self.assertIn(span.name, {"", "request", "buffer"})
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

class TimingReportTests(unittest.TestCase):
    """tools/timing_report.py: endpoint by hold, and fragment endings (#80)."""

    def test_fragment_endings_are_counted(self):
        for text, fragment in (("turn it down,", True), ("open the", True),
                               ("close it and", True), ("wait\u2026", True),
                               ("what time is it", False),
                               ("open Firefox.", False)):
            with self.subTest(text=text):
                self.assertEqual(timing_report.is_fragment(text), fragment)

    def test_the_report_prints_no_heard_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "session.log"
            log.write_text(
                "2026-09-24 12:00:00  heard   'ZEBRA-SENTINEL and'\n"
                "2026-09-24 12:00:02  TIMING  2.00s continuations=0 hold=0.80s"
                " endpoint=0.95s transcribe=0.20s\n"
                # Cut by the cap while still talking: below its hold.
                "2026-09-24 12:01:00  TIMING  1.00s continuations=0 hold=0.80s"
                " endpoint=0.05s transcribe=0.20s\n")
            out = io.StringIO()
            with mock.patch.object(timing_report.config, "LOG_FILE", log), \
                    mock.patch.object(sys, "argv", ["timing_report.py"]), \
                    contextlib.redirect_stdout(out):
                timing_report.main()
        printed = out.getvalue()
        self.assertNotIn("ZEBRA-SENTINEL", printed)
        self.assertIn("1 of 1", printed)
        self.assertIn("under hold: 1", printed)

    def test_timing_report_until_is_exclusive(self):
        """--since the "before" day --until the "after" day is main's window
        alone, and the "after" day is not in it (#135)."""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "session.log"
            log.write_text("".join(
                f"2026-09-0{day} 12:00:00  TIMING  3.00s continuations=0 "
                "speak=2.00s synth=0.30s\n" for day in (1, 2, 3)))

            def speak_n(*argv):
                out = io.StringIO()
                with mock.patch.object(timing_report.config, "LOG_FILE", log), \
                        mock.patch.object(sys, "argv", ["timing_report.py", *argv]), \
                        contextlib.redirect_stdout(out):
                    timing_report.main()
                row = next(r for r in out.getvalue().splitlines()
                           if r.startswith("speak "))
                return int(row.split()[1])

            self.assertEqual(speak_n("--since", "2026-09-01",
                                     "--until", "2026-09-03"), 2)
            self.assertEqual(speak_n("--since", "2026-09-03"), 1)


if __name__ == "__main__":
    unittest.main()
