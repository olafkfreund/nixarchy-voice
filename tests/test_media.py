"""#73: ask the media player what it is doing instead of photographing it.

`system_query(topic="media")` reads MPRIS through playerctl and
`media_control` sends one MPRIS command and reports what the player says
afterwards. Both go through `_shell`, faked here by argv.

Run with: nix develop -c python3 -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import mcp_server, tools  # noqa: E402
from omarchy_voice.config import Config, install_hint  # noqa: E402
from omarchy_voice.tools import (INPUT_TOOLS, MEDIA_FORMAT, READ_ONLY_TOOLS,  # noqa: E402
                                 SYSTEM_QUERIES, TOOL_SCHEMAS, Executor, Result,
                                 tools_for)

STATUS_ALL = ("playerctl", "-a", "--format", MEDIA_FORMAT, "status")
STATUS = ("playerctl", "status")
VAULT_HIDDEN = {"class": "1Password", "title": "1Password",
                "workspace": {"name": "9"}, "mapped": False}
ORDINARY = {"class": "foot", "title": "shell", "workspace": {"name": "1"},
            "mapped": True}


def executor(replies=None, locked=False, clients=None, dry_run=False):
    """An executor whose `_shell` answers from `replies`, keyed by argv.

    A value that is a list is consumed one reply per call, the last repeating.
    """
    ex = Executor(Config(dry_run=dry_run))
    ex.calls = []
    replies = dict(replies or {})

    def shell(cmd, **kw):
        ex.calls.append(tuple(cmd))
        got = replies.get(tuple(cmd), Result(True, ""))
        if isinstance(got, list):
            return got.pop(0) if len(got) > 1 else got[0]
        return got

    ex._shell = shell
    ex._session_is_locked = lambda: locked
    ex._query_rows = lambda kind: (clients or [], None)
    return ex


def which(name):
    return f"/bin/{name}"


class MediaStatusTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(tools.shutil, "which", which)
        patcher.start()
        self.addCleanup(patcher.stop)

    def status(self, ex):
        return ex._tool_system_query("media")

    def test_locked_session_refuses_and_never_runs_playerctl(self):
        ex = executor({STATUS_ALL: Result(True, "spotify\tPlaying\tA\tSong")}, locked=True)
        got = self.status(ex)
        self.assertFalse(got.ok)
        self.assertIn("locked", got.output)
        self.assertFalse([c for c in ex.calls if c[0] == "playerctl"])

    def test_no_player_says_nothing_is_playing(self):
        ex = executor({STATUS_ALL: Result(False, "No players found")})
        got = self.status(ex)
        self.assertTrue(got.ok)
        self.assertIn("nothing is playing", got.output)

    def test_spotify_row_prints_player_status_artist_title(self):
        ex = executor({STATUS_ALL: Result(True, "spotify\tPlaying\tSoundgarden\tBlack Hole Sun")})
        self.assertEqual(self.status(ex).output,
                         "spotify: Playing — Soundgarden – Black Hole Sun")

    def test_stripped_trailing_fields_are_padded(self):
        ex = executor({STATUS_ALL: Result(True, "chromium\tStopped")})
        self.assertEqual(self.status(ex).output, "chromium: Stopped")

    def test_sensitive_title_is_withheld_by_category(self):
        ex = executor({STATUS_ALL: Result(True, "spotify\tPlaying\tA\tYour one-time code")})
        got = self.status(ex).output
        self.assertNotIn("one-time code\n", got)
        self.assertNotIn("Your", got)
        self.assertIn("a credential or one-time code", got)

    def test_browser_title_withheld_while_vault_open_on_hidden_workspace(self):
        ex = executor({STATUS_ALL: Result(True, "chromium\tPlaying\tA\tSong")},
                      clients=[ORDINARY, VAULT_HIDDEN])
        got = self.status(ex).output
        self.assertNotIn("Song", got)
        self.assertIn("a password manager", got)
        self.assertIn("chromium: Playing", got)

    def test_browser_title_withheld_when_clients_fails(self):
        ex = executor({STATUS_ALL: Result(True, "chromium\tPlaying\tA\tSong")})
        ex._query_rows = lambda kind: ([], "hyprctl clients failed: x")
        got = self.status(ex).output
        self.assertNotIn("Song", got)
        self.assertIn("could not be read", got)

    def test_browser_title_shown_with_only_ordinary_windows(self):
        ex = executor({STATUS_ALL: Result(True, "chromium\tPlaying\tA\tSong")},
                      clients=[ORDINARY])
        self.assertIn("A – Song", self.status(ex).output)

    def test_non_browser_player_does_not_read_clients(self):
        ex = executor({STATUS_ALL: Result(True, "spotify\tPlaying\tA\tSong")})

        def boom(kind):
            raise AssertionError("clients read for a non-browser player")
        ex._query_rows = boom
        self.assertIn("A – Song", self.status(ex).output)

    def test_missing_playerctl_gives_install_hint(self):
        ex = executor()
        with mock.patch.object(tools.shutil, "which", lambda name: None):
            got = self.status(ex)
        self.assertFalse(got.ok)
        self.assertEqual(got.output, install_hint("playerctl", "playerctl"))

    def test_media_topic_in_enum_and_registry(self):
        schema = next(s for s in TOOL_SCHEMAS if s["name"] == "system_query")
        self.assertIn("media", schema["input_schema"]["properties"]["topic"]["enum"])
        self.assertIn("media", SYSTEM_QUERIES)


class MediaControlTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(tools.shutil, "which", which)
        patcher.start()
        self.addCleanup(patcher.stop)
        # A counter clock: each sleep advances time, so the poll's deadline is
        # reached without waiting.
        self.now = [0.0]
        for name, fake in (("monotonic", lambda: self.now[0]),
                           ("sleep", lambda s: self.now.__setitem__(0, self.now[0] + s))):
            patcher = mock.patch.object(tools.time, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def reads(self, ex):
        return sum(1 for c in ex.calls if c == STATUS)

    def test_play_then_playing_reports_playing(self):
        ex = executor({STATUS: [Result(True, "Paused"), Result(True, "Playing")]})
        got = ex._tool_media_control("play")
        self.assertTrue(got.ok)
        self.assertIn("now reports Playing", got.output)
        self.assertIn(("playerctl", "play"), ex.calls)

    def test_play_never_taking_gives_up_within_one_second(self):
        ex = executor({STATUS: Result(True, "Paused")})
        got = ex._tool_media_control("play")
        self.assertIn("may not have taken it", got.output)
        self.assertLessEqual(self.reads(ex), 11)
        self.assertLessEqual(self.now[0], 1.0 + 1e-9)

    def test_play_pause_expects_a_change_from_the_pre_read(self):
        ex = executor({STATUS: [Result(True, "Paused"), Result(True, "Playing")]})
        got = ex._tool_media_control("play-pause")
        self.assertIn("now reports Playing", got.output)
        self.assertEqual(ex.calls[0], STATUS)  # the pre-read comes first

    def test_next_does_one_status_read(self):
        ex = executor({STATUS: Result(True, "Playing")})
        got = ex._tool_media_control("next")
        self.assertTrue(got.ok)
        self.assertEqual(self.reads(ex), 1)

    def test_no_player_for_control_is_an_error(self):
        ex = executor({("playerctl", "pause"): Result(False, "No players found")})
        got = ex._tool_media_control("pause")
        self.assertFalse(got.ok)
        self.assertIn("nothing to pause", got.output)

    def test_unknown_action_refused_by_validator_also_under_dry_run(self):
        ex = executor(dry_run=True)
        got = ex.call("media_control", {"action": "shuffle"})
        self.assertFalse(got.ok)
        self.assertEqual(ex.calls, [])

    def test_dry_run_pause_is_described_not_sent(self):
        ex = executor(dry_run=True)
        got = ex.call("media_control", {"action": "pause"})
        self.assertEqual(got.output, "[dry-run] would run: media pause")
        self.assertEqual(ex.calls, [])

    def test_describe_media_control(self):
        self.assertEqual(Executor.describe("media_control", {"action": "pause"}), "media pause")

    def test_media_control_is_neither_read_only_nor_input(self):
        self.assertNotIn("media_control", READ_ONLY_TOOLS)
        self.assertNotIn("media_control", INPUT_TOOLS)

    def test_media_control_reaches_mcp(self):
        names = [t.name for t in mcp_server._to_mcp_tools(tools_for(Config()))]
        self.assertIn("media_control", names)

    def test_read_screen_description_points_at_media(self):
        schema = next(s for s in TOOL_SCHEMAS if s["name"] == "read_screen")
        self.assertIn("system_query media", schema["description"])


if __name__ == "__main__":
    unittest.main()
