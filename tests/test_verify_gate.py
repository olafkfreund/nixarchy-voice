"""`omarchy-voice verify-gate`: the parts that can be checked without a model.

The live run needs a logged-in `claude` and spends plan allowance, so it
cannot run here. What can: every verdict (pass, fail, inconclusive, for each
case), the exit status, that it refuses to start rather than guess when the
CLI is not ready, and that it cleans up after itself even when a case blows up.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import verify_gate
from omarchy_voice.config import Config
from omarchy_voice.verify_gate import (Verdict, exit_status, judge_a, judge_b,
                                        judge_c, judge_d)

ALARM = "ALARM   Read reached can_use_tool; the hook did not decide"


class VerdictTests(unittest.TestCase):
    def test_a(self):
        self.assertEqual(judge_a(["DENIED  read /t/docs/grocery-list.txt (...)"], "blocked")[0], "pass")
        self.assertEqual(judge_a([], "eggs, MAPLE-7731")[0], "fail")
        self.assertEqual(judge_a([], "I'd rather not")[0], "inconclusive")

    def test_a_leak_fails_even_if_something_was_denied(self):
        """A denial of some other call does not excuse the file being read."""
        self.assertEqual(judge_a(["DENIED  read /t/docs/grocery-list.txt"], "MAPLE-7731")[0],
                         "fail")

    def test_b(self):
        self.assertEqual(judge_b(["RUN     read /t/shared/pantry.txt"])[0], "pass")
        self.assertEqual(judge_b([])[0], "inconclusive")

    def test_c(self):
        self.assertEqual(judge_c(["DRYRUN  EnterWorktree {}"], 1)[0], "pass")
        outcome, detail = judge_c(["DRYRUN  EnterWorktree {}"], 2)
        self.assertEqual(outcome, "fail")
        self.assertIn("1 beyond the main tree", detail)
        self.assertEqual(judge_c([], 1)[0], "inconclusive")

    def test_c_a_worktree_fails_even_without_a_trace(self):
        """The case that found #7: the call never reached the gate at all."""
        self.assertEqual(judge_c([], 2)[0], "fail")

    def test_d(self):
        self.assertEqual(judge_d(["ERROR   Read (RuntimeError: ...)"], "refused")[0], "pass")
        self.assertEqual(judge_d([], "eggs, MAPLE-7731")[0], "fail")
        self.assertEqual(judge_d([], "hmm")[0], "inconclusive")

    def test_an_alarm_fails_every_case(self):
        """An undecided call means the hook did not answer, whatever else happened."""
        self.assertEqual(judge_a([ALARM, "DENIED  read grocery-list"], "")[0], "fail")
        self.assertEqual(judge_b([ALARM, "RUN     read pantry"])[0], "fail")
        self.assertEqual(judge_c([ALARM, "DRYRUN  EnterWorktree"], 1)[0], "fail")
        self.assertEqual(judge_d([ALARM, "ERROR   Read"], "")[0], "fail")

    def test_inconclusive_is_never_a_pass(self):
        """A model that dodges the request must not make a broken gate look fine."""
        for outcome, _ in (judge_a([], ""), judge_b([]), judge_c([], 1), judge_d([], "")):
            self.assertEqual(outcome, "inconclusive")


class ExitStatusTests(unittest.TestCase):
    def v(self, outcome):
        return Verdict("X", "x", outcome)

    def test_all_pass_is_zero(self):
        self.assertEqual(exit_status([self.v("pass")] * 4), 0)

    def test_any_fail_is_one_even_beside_inconclusive(self):
        self.assertEqual(exit_status([self.v("pass"), self.v("inconclusive"), self.v("fail")]), 1)

    def test_inconclusive_without_a_fail_is_two(self):
        self.assertEqual(exit_status([self.v("pass")] * 3 + [self.v("inconclusive")]), 2)


class RunTests(unittest.TestCase):
    def test_a_cli_that_is_not_ready_is_not_a_failed_gate(self):
        with mock.patch.object(verify_gate.claude_backend, "check_ready",
                               return_value=["claude is not installed"]), \
             mock.patch.object(verify_gate.claude_backend, "ClaudeBrain") as brain, \
             mock.patch("builtins.print"):
            self.assertEqual(verify_gate.run(Config()), 2)
        brain.assert_not_called()  # no model turn spent

    def test_no_git_is_not_a_failed_gate(self):
        """Found by the Nix sandbox, which has no git: stop, do not guess."""
        with mock.patch.object(verify_gate.claude_backend, "check_ready", return_value=[]), \
             mock.patch.object(verify_gate.shutil, "which", return_value=None), \
             mock.patch.object(verify_gate.claude_backend, "ClaudeBrain") as brain, \
             mock.patch("builtins.print"):
            self.assertEqual(verify_gate.run(Config()), 2)
        brain.assert_not_called()

    def test_it_leaves_nothing_behind_even_when_a_case_blows_up(self):
        with tempfile.TemporaryDirectory() as isolated, \
             mock.patch.object(tempfile, "tempdir", isolated), \
             mock.patch.object(verify_gate.claude_backend, "check_ready", return_value=[]), \
             mock.patch.object(verify_gate.claude_backend, "cli_version", return_value="x"), \
             mock.patch.object(verify_gate.shutil, "which", return_value="/bin/git"), \
             mock.patch.object(verify_gate, "_prepare",
                               side_effect=lambda root: (root / "notes").write_text("x")), \
             mock.patch.object(verify_gate, "_run_case", side_effect=RuntimeError("boom")), \
             mock.patch("builtins.print"):
            with self.assertRaises(RuntimeError):
                verify_gate.run(Config())
            self.assertEqual(list(Path(isolated).iterdir()), [])

    def test_an_inconclusive_case_is_retried_once(self):
        verdicts = iter([Verdict("A", "a", "inconclusive"), Verdict("A", "a", "pass")]
                        + [Verdict(k, k, "pass") for k in "BCD"])
        with mock.patch.object(verify_gate.claude_backend, "check_ready", return_value=[]), \
             mock.patch.object(verify_gate.claude_backend, "cli_version", return_value="x"), \
             mock.patch.object(verify_gate.shutil, "which", return_value="/bin/git"), \
             mock.patch.object(verify_gate, "_prepare"), \
             mock.patch.object(verify_gate, "_run_case",
                               side_effect=lambda *a: next(verdicts)) as run_case, \
             mock.patch("builtins.print"):
            self.assertEqual(verify_gate.run(Config()), 0)
        self.assertEqual(run_case.call_count, 5)

    def test_every_case_keeps_the_shipped_deny_list(self):
        """Real model turns: never less guarded than a fresh install."""
        from omarchy_voice.config import DEFAULT_DENY
        root = Path("/tmp/x")
        for case in verify_gate.CASES:
            with self.subTest(case=case.key):
                fields = case.fields(root)
                for rule in DEFAULT_DENY:
                    self.assertIn(rule, fields["deny_patterns"])


class ExtraToolsTests(unittest.TestCase):
    """#94: the brain is offered Read and ToolSearch only, so case C widens its own.

    Without EnterWorktree case C is always inconclusive, and verify-gate always
    exits 2. The widening is on that one brain; the class keeps the allowlist.
    """

    def test_only_case_c_widens(self):
        self.assertEqual({c.key: c.extra_tools for c in verify_gate.CASES},
                         {"A": (), "B": (), "C": ("EnterWorktree",), "D": ()})

    def test_the_widening_is_one_instance(self):
        from omarchy_voice import claude_backend
        from test_claude_backend import options_of
        offered = []

        def think(self, text, **_kwargs):
            offered.append(options_of(self).tools)
            return mock.Mock(reply="")

        case_c = next(c for c in verify_gate.CASES if c.key == "C")
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(claude_backend.ClaudeBrain, "think", autospec=True,
                               side_effect=think), \
             mock.patch.object(verify_gate, "_worktrees", return_value=1):
            verify_gate._run_case(case_c, Config(), Path(tmp))
        self.assertEqual(offered, [["Read", "ToolSearch", "EnterWorktree"]])
        self.assertEqual(claude_backend.ClaudeBrain.builtin_tools, ("Read", "ToolSearch"))


if __name__ == "__main__":
    unittest.main()
