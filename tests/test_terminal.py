"""Terminals through tmux, and telling you when something finishes.

A terminal used to be a picture: grim the window, run tesseract, hope. That
made output garbled, readable only while the window was visible, and invisible
entirely with the screen asleep. Input was worse — wtype into whatever had
focus, with no way to know it landed.

tmux answers all of it, and it is the one surface here where the assistant can
both read exactly and act reliably.

Run with: python3 -m unittest discover -s tests
"""

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice.config import Config
from omarchy_voice.tools import (
    IDLE_COMMANDS, READ_ONLY_TOOLS, TERMINAL_OUTPUT_LIMIT, WATCH_MAX_SECONDS,
    Executor, Result,
)

PANES = "\n".join([
    "Work\t1\t1\t1\tbash\t~/code",
    "Work\t1\t1\t2\tpytest\trunning tests",
    "Spare\t0\t1\t1\tbash\t~/elsewhere",
])


class FakeTmux(Executor):
    """An executor with a scripted tmux and a desktop that has a terminal on it."""

    def __init__(self, panes=PANES, capture="build ok", terminal_visible=True,
                 announces=True, allow_shell=True):
        # The shell is on: these test the handler. With it off, run_in_terminal
        # is held for a yes first; that is test_shell_off (#112).
        super().__init__(Config(allow_shell=allow_shell))
        # A daemon polls these; the default is one, as WatchingTests assume.
        self.announces_watches = announces
        self.panes_raw = panes
        self.capture = capture
        self.sent: list[list[str]] = []
        self.launched: list[list[str]] = []
        self._terminal_on_screen = lambda: terminal_visible

    def _shell(self, cmd, **kwargs):        # type: ignore[override]
        self.sent.append(cmd)
        if cmd[0] != "tmux":
            self.launched.append(cmd)
            return Result(True, "started")
        verb = cmd[1]
        if verb == "list-panes":
            return Result(True, self.panes_raw)
        if verb == "capture-pane":
            return Result(True, self.capture)
        return Result(True, "")


def with_tmux(**kwargs):
    ex = FakeTmux(**kwargs)
    with mock.patch("shutil.which", return_value="/usr/bin/tmux"):
        yield ex


class PaneListingTests(unittest.TestCase):
    def setUp(self):
        self.ex = FakeTmux()
        self.which = mock.patch("shutil.which", return_value="/usr/bin/tmux")
        self.which.start()
        self.addCleanup(self.which.stop)

    def test_panes_are_parsed_into_targets(self):
        panes = self.ex._tmux_panes()
        self.assertEqual([p["target"] for p in panes],
                         ["Work:1.1", "Work:1.2", "Spare:1.1"])

    def test_a_shell_means_idle_and_anything_else_means_busy(self):
        panes = {p["target"]: p for p in self.ex._tmux_panes()}
        self.assertTrue(panes["Work:1.1"]["idle"])
        self.assertFalse(panes["Work:1.2"]["idle"])
        self.assertIn("bash", IDLE_COMMANDS)

    def test_attachment_is_read_per_session(self):
        panes = {p["target"]: p for p in self.ex._tmux_panes()}
        self.assertTrue(panes["Work:1.1"]["attached"])
        self.assertFalse(panes["Spare:1.1"]["attached"])

    def test_an_empty_target_prefers_the_pane_with_something_running(self):
        pane, why = self.ex._resolve_pane("")
        self.assertEqual(pane["target"], "Work:1.2")

    def test_an_exact_target_wins(self):
        self.assertEqual(self.ex._resolve_pane("Work:1.1")[0]["target"], "Work:1.1")

    def test_an_ambiguous_target_is_refused_with_the_options(self):
        pane, why = self.ex._resolve_pane("Work")
        self.assertIsNone(pane)
        self.assertIn("Work:1.1", why)
        self.assertIn("Work:1.2", why)

    def test_no_tmux_at_all_says_how_to_start_one(self):
        ex = FakeTmux(panes="")
        pane, why = ex._resolve_pane("")
        self.assertIsNone(pane)
        self.assertIn("launch terminal tmux", why)


class ReadingTests(unittest.TestCase):
    def setUp(self):
        self.which = mock.patch("shutil.which", return_value="/usr/bin/tmux")
        self.which.start()
        self.addCleanup(self.which.stop)

    def test_reading_is_exact_text_not_a_screenshot(self):
        ex = FakeTmux(capture="error: cannot find module 'foo'")
        result = ex.call("read_terminal", {})
        self.assertIn("cannot find module", result.output)
        self.assertFalse(any("grim" in c[0] or "tesseract" in c[0] for c in ex.sent))

    def test_the_read_says_whether_the_pane_is_still_busy(self):
        ex = FakeTmux()
        self.assertIn("still running", ex.call("read_terminal", {}).output)
        self.assertIn("idle", ex.call("read_terminal", {"target": "Work:1.1"}).output)

    def test_a_pane_nobody_can_see_is_still_readable(self):
        """The whole point: workspace and DPMS do not apply to text."""
        ex = FakeTmux(terminal_visible=False)
        self.assertTrue(ex.call("read_terminal", {"target": "Spare:1.1"}).ok)

    def test_a_huge_scrollback_is_trimmed_from_the_top(self):
        ex = FakeTmux(capture="x" * (TERMINAL_OUTPUT_LIMIT * 2) + "THE-END")
        out = ex.call("read_terminal", {}).output
        self.assertLess(len(out), TERMINAL_OUTPUT_LIMIT + 400)
        self.assertIn("THE-END", out)          # the newest output survives
        self.assertIn("earlier output", out)

    def test_reading_and_listing_work_under_dry_run(self):
        self.assertIn("read_terminal", READ_ONLY_TOOLS)
        self.assertIn("list_terminals", READ_ONLY_TOOLS)


class RunningTests(unittest.TestCase):
    def setUp(self):
        self.which = mock.patch("shutil.which", return_value="/usr/bin/tmux")
        self.which.start()
        self.addCleanup(self.which.stop)

    def test_a_command_is_sent_with_a_literal_Enter(self):
        """send-keys takes the key by name, so none of the keysym problem that
        cost a whole session applies here."""
        ex = FakeTmux()
        with mock.patch("time.sleep"):
            ex.call("run_in_terminal", {"command": "ls", "target": "Work:1.1"})
        keys = next(c for c in ex.sent if c[:2] == ["tmux", "send-keys"])
        self.assertEqual(keys[-2:], ["ls", "Enter"])

    def test_the_command_is_passed_after_a_double_dash(self):
        ex = FakeTmux()
        with mock.patch("time.sleep"):
            ex.call("run_in_terminal", {"command": "--version", "target": "Work:1.1"})
        keys = next(c for c in ex.sent if c[:2] == ["tmux", "send-keys"])
        self.assertIn("--", keys)

    def test_a_pane_nobody_can_see_is_refused(self):
        """Chosen behaviour: an open microphone may not run commands where the
        user cannot watch them happen."""
        ex = FakeTmux(terminal_visible=False)
        result = ex.call("run_in_terminal", {"command": "ls", "target": "Work:1.1"})
        self.assertFalse(result.ok)
        self.assertIn("not on screen", result.output)
        self.assertEqual([c for c in ex.sent if c[:2] == ["tmux", "send-keys"]], [])

    def test_a_detached_session_is_refused_even_with_a_terminal_on_screen(self):
        ex = FakeTmux()
        result = ex.call("run_in_terminal", {"command": "ls", "target": "Spare:1.1"})
        self.assertFalse(result.ok)
        self.assertIn("not on screen", result.output)

    def test_typing_into_a_busy_pane_is_refused(self):
        """Keys sent to a pane running vim go to vim, not to a shell."""
        ex = FakeTmux()
        result = ex.call("run_in_terminal", {"command": "ls", "target": "Work:1.2"})
        self.assertFalse(result.ok)
        self.assertIn("busy", result.output)
        self.assertIn("pytest", result.output)

    def test_an_empty_command_is_refused(self):
        self.assertFalse(FakeTmux().call("run_in_terminal", {"command": "  "}).ok)

    def test_newlines_are_refused_rather_than_run_as_a_script(self):
        result = FakeTmux().call("run_in_terminal", {"command": "ls\nrm -rf /"})
        self.assertFalse(result.ok)

    def test_the_refusal_names_the_way_that_does_work(self):
        """Given only "newlines are not sent", the model retried the same
        heredoc five times, then took thirteen commands and forty-five seconds
        to write a two-line file."""
        result = FakeTmux().call("run_in_terminal",
                                 {"command": "cat > f <<EOF\nhello\nEOF"})
        self.assertIn("printf", result.output)
        self.assertIn(">>", result.output)

    def test_the_deny_list_still_applies(self):
        ex = FakeTmux()
        result = ex.call("run_in_terminal", {"command": "sudo rm -rf /"})
        self.assertFalse(result.ok)
        self.assertEqual([c for c in ex.sent if c[:2] == ["tmux", "send-keys"]], [])

    def test_the_gate_sees_the_actual_command(self):
        self.assertIn("rm -rf",
                      Executor.describe("run_in_terminal", {"command": "rm -rf /tmp/x"}))


class WatchingTests(unittest.TestCase):
    def setUp(self):
        self.which = mock.patch("shutil.which", return_value="/usr/bin/tmux")
        self.which.start()
        self.addCleanup(self.which.stop)

    def test_watching_a_busy_pane_returns_at_once(self):
        """It must not block: the microphone is open while it waits."""
        ex = FakeTmux()
        result = ex.call("watch_terminal", {"target": "Work:1.2"})
        self.assertTrue(result.ok)
        self.assertIn("Work:1.2", ex._watches)

    def test_watching_an_idle_pane_is_refused_with_what_it_last_showed(self):
        ex = FakeTmux(capture="all 42 tests passed")
        result = ex.call("watch_terminal", {"target": "Work:1.1"})
        self.assertFalse(result.ok)
        self.assertIn("already idle", result.output)
        self.assertIn("42 tests passed", result.output)

    def test_nothing_fires_while_the_command_is_still_running(self):
        ex = FakeTmux()
        ex.watch("Work:1.2", "the tests")
        self.assertEqual(ex.poll_watches(), [])

    def test_a_watch_does_not_fire_before_the_shell_has_even_forked(self):
        """`pane_current_command` still says "bash" for a moment after keys are
        sent. Reporting that as finished handed back a twenty-second command as
        done in under half a second."""
        ex = FakeTmux(panes=PANES.replace("pytest", "bash"))
        ex.watch("Work:1.2", "the tests")          # not yet observed busy
        self.assertEqual(ex.poll_watches(), [])

    def test_an_instant_command_is_reported_once_the_grace_passes(self):
        from omarchy_voice.tools import TERMINAL_START_GRACE
        ex = FakeTmux(panes=PANES.replace("pytest", "bash"))
        ex.watch("Work:1.2", "cd somewhere")
        ex._watches["Work:1.2"]["started"] -= TERMINAL_START_GRACE + 1
        [job] = ex.poll_watches()
        self.assertFalse(job["timed_out"])

    def test_watch_terminal_knows_the_pane_was_already_busy(self):
        ex = FakeTmux()
        ex.call("watch_terminal", {"target": "Work:1.2"})
        self.assertTrue(ex._watches["Work:1.2"]["seen_busy"])

    def test_a_pane_returning_to_the_shell_is_the_done_signal(self):
        ex = FakeTmux()
        ex.watch("Work:1.2", "the tests")
        self.assertEqual(ex.poll_watches(), [])    # busy: records that it started
        ex.panes_raw = PANES.replace("pytest", "bash")
        [job] = ex.poll_watches()
        self.assertEqual(job["label"], "the tests")
        self.assertFalse(job["vanished"])
        self.assertIn("build ok", job["tail"])

    def test_a_finished_job_is_announced_once(self):
        ex = FakeTmux()
        ex.watch("Work:1.2", "the tests", seen_busy=True)
        ex.panes_raw = PANES.replace("pytest", "bash")
        self.assertEqual(len(ex.poll_watches()), 1)
        self.assertEqual(ex.poll_watches(), [])

    def test_a_closed_pane_is_reported_rather_than_watched_forever(self):
        ex = FakeTmux()
        ex.watch("Work:1.2", "the tests", seen_busy=True)
        ex.panes_raw = "Work\t1\t1\t1\tbash\t~/code"
        [job] = ex.poll_watches()
        self.assertTrue(job["vanished"])

    def test_a_watch_on_something_that_never_ends_still_gives_up(self):
        """The pane stays busy forever. An early `continue` used to skip the age
        check entirely, so this watch would have been held until the daemon
        restarted."""
        ex = FakeTmux()
        ex.watch("Work:1.2", "the tests", seen_busy=True)
        ex._watches["Work:1.2"]["started"] -= WATCH_MAX_SECONDS + 1
        [job] = ex.poll_watches()
        self.assertTrue(job["timed_out"])
        self.assertEqual(ex._watches, {})

    def test_watch_terminal_promises_nothing_when_nothing_announces(self):
        """Nothing polls in `say` or the MCP server, so a promise there is a lie (#74)."""
        ex = FakeTmux(announces=False)
        result = ex.call("watch_terminal", {"target": "Work:1.2"})
        self.assertFalse(result.ok)
        self.assertIn("Nothing in this process will say", result.output)
        self.assertNotIn("I will say", result.output)
        self.assertEqual(ex._watches, {})

    def test_an_idle_pane_is_still_refused_as_idle_first(self):
        result = FakeTmux(announces=False).call("watch_terminal", {"target": "Work:1.1"})
        self.assertIn("already idle", result.output)

    def run_long(self, announces):
        ex = FakeTmux(announces=announces)
        with mock.patch("omarchy_voice.tools.TERMINAL_QUICK_WAIT", 0):
            return ex, ex.call("run_in_terminal", {"command": "make", "target": "Work:1.1"})

    def test_a_long_run_promises_nothing_when_nothing_announces(self):
        ex, result = self.run_long(announces=False)
        self.assertTrue(result.ok)
        self.assertIn("Nothing here will say", result.output)
        self.assertNotIn("I am watching", result.output)
        self.assertEqual(ex._watches, {})

    def test_a_long_run_is_watched_where_something_announces(self):
        ex, result = self.run_long(announces=True)
        self.assertTrue(result.ok)
        self.assertIn("will say when it finishes", result.output)
        self.assertIn("Work:1.1", ex._watches)

    def test_polling_nothing_costs_nothing(self):
        ex = FakeTmux()
        self.assertEqual(ex.poll_watches(), [])
        self.assertEqual(ex.sent, [])


class VisibilityTests(unittest.TestCase):
    """`session_attached` says a client exists, not that anyone can see it —
    the client may be in a window on a workspace nobody has looked at today."""

    def setUp(self):
        self.ex = Executor(Config())
        self.ex._visible_workspaces = lambda: {"2"}

    def windows(self, *clients):
        self.ex._query_json = lambda kind: list(clients)

    def test_a_terminal_on_a_visible_workspace_counts(self):
        self.windows({"class": "foot", "workspace": {"name": "2"}})
        self.assertTrue(self.ex._terminal_on_screen())

    def test_a_terminal_on_another_workspace_does_not(self):
        self.windows({"class": "foot", "workspace": {"name": "7"}})
        self.assertFalse(self.ex._terminal_on_screen())

    def test_a_browser_is_not_a_terminal(self):
        self.windows({"class": "chrome-x.com__-Default", "workspace": {"name": "2"}})
        self.assertFalse(self.ex._terminal_on_screen())

    def test_the_common_terminals_are_recognised(self):
        for klass in ("foot", "Alacritty", "kitty", "com.mitchellh.ghostty",
                      "org.wezfurlong.wezterm", "org.omarchy.voice-terminal"):
            with self.subTest(klass=klass):
                self.windows({"class": klass, "workspace": {"name": "2"}})
                self.assertTrue(self.ex._terminal_on_screen())


# -- secrets on screen (#101) ------------------------------------------------
# Every fake secret is built by joining strings, and split at its delimiter, so
# no committed line looks like a real token and the corpus test below stays
# green on this very file.
PROMPT = "~ $"
PEM_BEGIN = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
PEM_END = "-----END " + "OPENSSH PRIVATE KEY-----"
GH_TOKEN = "ghp_" + "a" * 36
SECRET_PARTS = ["Q" * 64, "R" * 64, "S" * 20, "a" * 36, "hunter2", "b" * 40,
                "C" * 16, "d" * 40, "e" * 24, "F" * 16, "Z" * 58]
KINDS = ("a private key", "a token", "a password in a URL", "a secret setting")
BUILD_LINES = [f"CC obj{i}.o" for i in range(197)]

SECRET_PANES = {
    # name: (lines, withheld, kinds, must survive)
    "pem": (["$ cat ~/.ssh/id_ed25519", PEM_BEGIN, "Q" * 64, "R" * 64, "S" * 20,
             PEM_END, PROMPT],
            5, ["a private key"], ["$ cat ~/.ssh/id_ed25519"]),
    "pem_scrolled": (["Q" * 64, "R" * 64, PEM_END, "$ ls", "a b c", PROMPT],
                     3, ["a private key"], ["$ ls", "a b c"]),
    "op_read": (["$ op read op://Private/GitHub/token", GH_TOKEN, PROMPT],
                1, ["a token"], ["op read op://Private/GitHub/token"]),
    "env": (["$ cat .env", "DEBUG=True", "PORT=8080",
             "DATABASE_URL=postgres://app:" + "hunter2" + "@db:5432/app",
             "OPENAI_API_KEY=" + "sk-proj-" + "b" * 40,
             "AWS_ACCESS_KEY_ID=" + "AKIA" + "C" * 16,
             "AWS_SECRET_ACCESS_KEY=" + "d" * 40,
             "STRIPE_WEBHOOK_SECRET=" + "whsec_" + "e" * 24, PROMPT],
            5, ["a password in a URL", "a secret setting", "a token"],
            ["DEBUG=True", "PORT=8080"]),
    "build_log": (["$ make deploy", *BUILD_LINES,
                   "export AWS_ACCESS_KEY_ID=" + "ASIA" + "F" * 16, "deploy ok", PROMPT],
                  1, ["a token"], ["$ make deploy", *BUILD_LINES, "deploy ok", PROMPT]),
    "age": (["$ cat key.txt", "# public key: age1" + "qz" * 29,
             "AGE-SECRET-" + "KEY-1" + "Z" * 58, "$ cat note.age",
             "-----BEGIN AGE " + "ENCRYPTED FILE-----",
             "YWdlLWVuY3J5cHRpb24ub3JnL3YxCi0", "-----END AGE " + "ENCRYPTED FILE-----"],
            1, ["a private key"],
            ["-----BEGIN AGE ENCRYPTED FILE-----", "YWdlLWVuY3J5cHRpb24ub3JnL3YxCi0"]),
}
CLEAN_PANES = {
    "git_log": ["$ git log --oneline -3",
                "a1b2c3d fix: stop reading ~/.ssh/config twice",
                "e4f5a6b chore: add .env to .gitignore",
                "0c9d8e7 docs: never put a password in the README", PROMPT],
    "python": ["$ cat check.py", "import os",
               "def check(password: str) -> bool:",
               '    API_KEY = os.environ["API_KEY"]',
               "    return password == API_KEY", PROMPT],
}
MARKER = re.compile(r"^\[withheld: (?:%s)\]$" % "|".join(KINDS))
HEADER = re.compile(r"^\[\d+ line\(s\) withheld: (?:%s)(?:, (?:%s))*; "
                    r"the rest is as on screen\]$" % ("|".join(KINDS), "|".join(KINDS)))


def header(count, kinds):
    return f"[{count} line(s) withheld: {', '.join(kinds)}; the rest is as on screen]"


def through_every_path(capture):
    """What the model is handed for this pane on each of the four paths."""
    from omarchy_voice import realtime
    outs = {}
    with mock.patch("shutil.which", return_value="/usr/bin/tmux"):
        outs["read_terminal"] = FakeTmux(capture=capture).call(
            "read_terminal", {"target": "Work:1.1"}).output
        with mock.patch("time.sleep"), \
                mock.patch("omarchy_voice.tools.TERMINAL_START_GRACE", -1):
            ran = FakeTmux(capture=capture).call(
                "run_in_terminal", {"command": "ls", "target": "Work:1.1"})
        outs["run_in_terminal"] = ran.output
        watched = FakeTmux(capture=capture).call("watch_terminal", {"target": "Work:1.1"})
        assert not watched.ok and "already idle" in watched.output
        outs["watch_terminal"] = watched.output
        ex = FakeTmux(capture=capture)
        ex.watch("Work:1.2", "deploy", seen_busy=True)
        ex.panes_raw = PANES.replace("pytest", "bash")
        [job] = ex.poll_watches()
        outs["poll_watches"] = job["tail"]
        outs["watch_message"] = realtime.watch_message(job)
    assert outs["run_in_terminal"].startswith("ran 'ls'")
    return outs


class SecretsOnScreenTests(unittest.TestCase):
    """A pane that just printed a secret must not reach the model verbatim (#101)."""

    def assert_no_secret(self, out):
        for part in SECRET_PARTS:
            self.assertNotIn(part, out)
            for i in range(len(part) - 7):
                self.assertNotIn(part[i:i + 8], out)

    def assert_markers_quote_nothing(self, out):
        for line in out.splitlines():
            if "withheld" in line:
                self.assertTrue(MARKER.match(line) or HEADER.match(line), line)

    def test_every_path_withholds_the_secret_lines(self):
        for name, (lines, count, kinds, survive) in SECRET_PANES.items():
            for path, out in through_every_path("\n".join(lines)).items():
                with self.subTest(pane=name, path=path):
                    self.assert_no_secret(out)
                    self.assertIn(header(count, kinds), out)
                    self.assertEqual(sum(1 for ln in out.splitlines() if MARKER.match(ln)),
                                     count)
                    for keep in survive:
                        self.assertIn(keep, out)

    def test_every_marker_on_every_path_quotes_nothing(self):
        for name, (lines, *_rest) in SECRET_PANES.items():
            for path, out in through_every_path("\n".join(lines)).items():
                with self.subTest(pane=name, path=path):
                    self.assertIn("withheld", out)
                    self.assert_markers_quote_nothing(out)
                    self.assert_no_secret(out)

    def test_a_pane_with_no_secret_is_untouched_on_every_path(self):
        for name, lines in CLEAN_PANES.items():
            pane = "\n".join(lines)
            for path, out in through_every_path(pane).items():
                with self.subTest(pane=name, path=path):
                    self.assertIn(pane, out)
                    self.assertNotIn("withheld", out)

    def test_the_build_log_keeps_everything_but_the_key(self):
        lines = SECRET_PANES["build_log"][0]
        outs = through_every_path("\n".join(lines))
        body = outs["read_terminal"].split("\n", 2)[2]    # after title and header
        self.assertEqual(len(body.splitlines()), 201)
        self.assertEqual(sum(1 for ln in body.splitlines() if ln in lines), 200)
        self.assertIn("deploy ok", outs["watch_message"])

    def test_redaction_happens_before_the_cut(self):
        """Cutting first would start 21 characters into the token and leave a
        fragment that no longer matches."""
        capture = ("$ op read x\n" + GH_TOKEN + "\n"
                   + "y" * (TERMINAL_OUTPUT_LIMIT - 20))
        with mock.patch("shutil.which", return_value="/usr/bin/tmux"):
            out = FakeTmux(capture=capture).call("read_terminal", {}).output
        self.assertNotIn("a" * 16, out)
        self.assertIn(header(1, ["a token"]), out)
        self.assertIn("earlier output not shown", out)
        self.assertLess(out.index("line(s) withheld"), out.index("earlier output"))

    def test_a_key_whose_begin_is_cut_off_is_still_withheld(self):
        capture = "\n".join([PEM_BEGIN, "Q" * 64, "R" * 64, PEM_END,
                             "y" * (TERMINAL_OUTPUT_LIMIT - 100)])
        with mock.patch("shutil.which", return_value="/usr/bin/tmux"):
            out = FakeTmux(capture=capture).call("read_terminal", {}).output
        self.assertNotIn("Q" * 16, out)
        self.assertNotIn("R" * 16, out)
        self.assertIn("a private key", out)

    def test_the_description_says_lines_are_withheld_and_it_is_a_heuristic(self):
        from omarchy_voice.tools import TOOL_SCHEMAS
        [schema] = [t for t in TOOL_SCHEMAS if t["name"] == "read_terminal"]
        self.assertIn("withheld", schema["description"])
        self.assertIn("heuristic", schema["description"])


class WithholdSecretsTests(unittest.TestCase):
    def withhold(self, *lines):
        from omarchy_voice.tools import withhold_secrets
        return withhold_secrets("\n".join(lines))

    def test_a_key_already_scrolling_off_the_top_is_withheld_back_to_its_start(self):
        text, kinds = self.withhold(*SECRET_PANES["pem_scrolled"][0])
        self.assertEqual(kinds, ["a private key"] * 3)
        self.assertIn("$ ls", text.splitlines())

    def test_prose_quoting_a_begin_header_starts_nothing(self):
        prose = [f'The file starts with "{PEM_BEGIN}" and then base64.',
                 *[f"line {i} of an explanation" for i in range(20)]]
        self.assertEqual(self.withhold(*prose), ("\n".join(prose), []))

    def test_an_unclosed_key_stops_at_the_first_line_that_is_not_key(self):
        text, kinds = self.withhold(PEM_BEGIN, "Q" * 64, "$ echo done", "after")
        self.assertEqual(kinds, ["a private key"] * 2)
        self.assertEqual(text.splitlines()[2:], ["$ echo done", "after"])

    def test_age_armor_is_ciphertext_and_the_identity_is_not(self):
        lines = SECRET_PANES["age"][0]
        text, kinds = self.withhold(*lines)
        self.assertEqual(kinds, ["a private key"])
        self.assertEqual(text.splitlines()[2], "[withheld: a private key]")
        self.assertEqual(text.splitlines()[4:], lines[4:])

    def test_code_and_known_misses_are_left_alone(self):
        for line in ('    API_KEY = os.environ["API_KEY"]',
                     'INSIDE_TOKEN = "MAPLE-7731"',
                     "password=" + "correcthorse"):   # lower case: a known miss
            with self.subTest(line=line):
                self.assertEqual(self.withhold(line), (line, []))

    def test_a_token_in_a_setting_is_reported_as_a_token(self):
        self.assertEqual(self.withhold("export AWS_ACCESS_KEY_ID=" + "ASIA" + "F" * 16)[1],
                         ["a token"])

    def test_one_kind_per_withheld_line_sorted(self):
        text, kinds = self.withhold(*SECRET_PANES["env"][0])
        self.assertEqual(kinds, ["a password in a URL", "a secret setting",
                                 "a secret setting", "a token", "a token"])
        self.assertEqual(len(text.splitlines()), 9)

    def test_nothing_in_this_repo_s_code_is_flagged(self):
        """The false-positive bar (decision 7). When this fails, tighten the
        pattern; never allow-list the file."""
        from omarchy_voice.tools import withhold_secrets
        root = Path(__file__).resolve().parent.parent
        files = sorted([*root.glob("src/**/*.py"), *root.glob("tests/**/*.py")])
        self.assertGreater(len(files), 20)
        for path in files:
            text = path.read_text(encoding="utf-8")
            out, kinds = withhold_secrets(text)
            if kinds:
                hit = next(i for i, (a, b) in enumerate(
                    zip(text.splitlines(), out.splitlines())) if a != b)
                self.fail(f"{path.relative_to(root)}:{hit + 1} flagged as {kinds}")


if __name__ == "__main__":
    unittest.main()
