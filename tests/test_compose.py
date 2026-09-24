"""Composition, and the mistakes from the session log that are now caught here.

Run with: python3 -m unittest discover -s tests
"""

import itertools
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import config as config_mod
from omarchy_voice.config import Config
from omarchy_voice.tools import (
    TERMINAL_PANE_ID, Executor, Result, _check_dispatch_args, _desktop_wm_class,
    _launch_text, _layout_plan, _pane_command, _pane_hint, _window_matches,
    normalise_omarchy,
)


class LayoutPlanTests(unittest.TestCase):
    def test_columns_walk_rightwards(self):
        self.assertEqual(_layout_plan("columns", 4),
                         [("r", 0), ("r", 1), ("r", 2)])

    def test_main_and_side_stacks_down_the_side(self):
        # Pane 0 keeps the left; 1 opens beside it, the rest below the one before.
        self.assertEqual(_layout_plan("main-and-side", 4),
                         [("r", 0), ("d", 1), ("d", 2)])

    def test_grid_fills_two_by_two(self):
        self.assertEqual(_layout_plan("grid", 4),
                         [("r", 0), ("d", 0), ("d", 1)])

    def test_a_plan_has_one_step_per_pane_after_the_first(self):
        for layout in ("columns", "main-and-side", "grid"):
            for count in range(1, 7):
                with self.subTest(layout=layout, count=count):
                    self.assertEqual(len(_layout_plan(layout, count)), max(0, count - 1))

    def test_every_step_anchors_on_a_pane_already_open(self):
        for layout in ("columns", "main-and-side", "grid"):
            for count in range(2, 7):
                for index, (_, anchor) in enumerate(_layout_plan(layout, count)):
                    with self.subTest(layout=layout, count=count, index=index):
                        # Pane index+1 is being placed, so panes 0..index exist.
                        self.assertLessEqual(anchor, index)


class PaneCommandTests(unittest.TestCase):
    def test_web_pane_needs_an_http_url(self):
        self.assertIsNone(_pane_command("web", "file:///etc/passwd", ""))
        self.assertIsNone(_pane_command("web", "news.ycombinator.com", ""))
        self.assertEqual(_pane_command("web", "https://apnews.com", "AP"),
                         ["omarchy", "launch", "webapp", "https://apnews.com"])

    def test_panes_never_launch_or_focus(self):
        # Composing means new windows. launch-or-focus would steal a window
        # from another workspace instead of opening one here.
        for kind, target in (("web", "https://apnews.com"), ("terminal", ""),
                             ("tui", "btop")):
            with self.subTest(kind=kind):
                self.assertNotIn("focus", _pane_command(kind, target, ""))

    def test_app_pane_rejects_a_command_line(self):
        self.assertIsNone(_pane_command("app", "rm -rf /", ""))
        self.assertIsNone(_pane_command("app", "chromium --incognito", ""))

    def test_tui_app_id_is_sanitised(self):
        argv = _pane_command("tui", "btop", "my name; rm -rf /")
        self.assertEqual(argv[3], "--app-id=mynamerm-rf")

    def test_the_terminal_argv_carries_the_id(self):
        # Before the command's own words, so xdg-terminal-exec takes it as its
        # option rather than passing it to the command (#87).
        self.assertEqual(_pane_command("terminal", "", ""),
                         ["omarchy", "launch", "terminal", "--app-id=org.omarchy.voice-terminal"])
        self.assertEqual(_pane_command("terminal", "htop -d 5", "")[3:],
                         ["--app-id=org.omarchy.voice-terminal", "htop", "-d", "5"])

    def test_the_tui_hint_is_its_app_id(self):
        # A name that sanitises to nothing launched as btop and hinted "" (#87).
        for target, name in (("btop", "!!!"), ("btop", ""), ("btop -d 5", "My Mon")):
            with self.subTest(target=target, name=name):
                self.assertEqual(_pane_hint("tui", target, name),
                                 _pane_command("tui", target, name)[3].removeprefix("--app-id="))


class WindowMatchTests(unittest.TestCase):
    """A composition once claimed a Chrome "Profile error occurred" dialog as
    its first pane, shifting every later pane by one."""

    DIALOG = {"class": "", "initialClass": "", "focusHistoryID": 0,
              "address": "0xdialog", "title": "Profile error occurred",
              "initialTitle": "Profile error occurred"}
    AP = {"class": "chrome-apnews.com__-Default", "initialClass": "chrome-apnews.com__-Default",
          "address": "0xap", "focusHistoryID": 1,
          "title": "Associated Press News", "initialTitle": "apnews.com"}

    def test_hints_come_off_the_target(self):
        self.assertEqual(_pane_hint("web", "https://www.bbc.com/news", ""), "bbc.com")
        self.assertEqual(_pane_hint("web", "https://apnews.com", ""), "apnews.com")
        self.assertEqual(_pane_hint("app", "spotify.desktop", ""), "spotify")
        self.assertEqual(_pane_hint("terminal", "", ""), TERMINAL_PANE_ID)

    def test_a_dialog_does_not_match_the_site(self):
        self.assertFalse(_window_matches(self.DIALOG, "apnews.com"))

    def test_the_real_window_matches_on_its_initial_title(self):
        # The page retitles itself once loaded, so initialTitle is what matches.
        self.assertTrue(_window_matches(
            {"class": "", "initialTitle": "www.bbc.com_/news", "title": "BBC News"}, "bbc.com"))

    def test_an_unclassed_window_is_never_claimed(self):
        executor = Executor(Config())
        with mock.patch.object(executor, "_query_rows",
                               return_value=([self.DIALOG], None)):
            self.assertIsNone(executor._await_new_window(set(), 0.4, "apnews.com"))

    def test_the_matching_window_wins_over_a_dialog(self):
        executor = Executor(Config())
        with mock.patch.object(executor, "_query_rows",
                               return_value=([self.DIALOG, self.AP], None)):
            self.assertEqual(
                executor._await_new_window(set(), 1.0, "apnews.com"), "0xap")

    def test_an_unhinted_pane_takes_nothing(self):
        # "" used to mean any classed window, and a terminal pane took Discord (#87).
        executor = Executor(Config())
        with mock.patch.object(executor, "_query_rows",
                               return_value=([self.DIALOG, self.AP], None)):
            self.assertIsNone(executor._await_new_window(set(), 1.0, ""))


class ComposeValidationTests(unittest.TestCase):
    def setUp(self):
        self.executor = Executor(Config())

    def validate(self, **kwargs):
        return self.executor._validate_compose_windows(**kwargs)

    def test_empty_panes_rejected(self):
        self.assertIsNotNone(self.validate(panes=[]))

    def test_too_many_panes_rejected(self):
        panes = [{"kind": "terminal", "target": ""}] * 9
        self.assertIn("unreadable", self.validate(panes=panes))

    def test_unknown_layout_rejected(self):
        self.assertIsNotNone(self.validate(
            panes=[{"kind": "terminal", "target": ""}], layout="cascade"))

    def test_bad_pane_names_which_one(self):
        error = self.validate(panes=[{"kind": "terminal", "target": ""},
                                     {"kind": "web", "target": "ftp://x"}])
        self.assertIn("pane 2", error)

    def test_a_valid_composition_passes(self):
        self.assertIsNone(self.validate(
            panes=[{"kind": "web", "target": "https://apnews.com"},
                   {"kind": "terminal", "target": ""}],
            layout="columns", workspace="next"))


class ComposePolicyTests(unittest.TestCase):
    """Composition must not become a way around the deny list."""

    def test_the_outer_gate_sees_the_whole_composition(self):
        # `describe` spells out every pane, so a deny pattern matching any of
        # them stops the call before the executor runs a thing.
        executor = Executor(Config(deny_patterns=[r"\bapnews\b"]))
        with mock.patch.object(Executor, "_shell") as shell:
            result = executor.call("compose_windows", {
                "panes": [{"kind": "web", "target": "https://apnews.com"}],
                "workspace": "current"})
        self.assertFalse(result.ok)
        self.assertIn("refused", result.output)
        shell.assert_not_called()

    def test_each_pane_is_checked_against_the_command_it_will_run(self):
        # The pane's label is all the outer gate sees; the inner check is what
        # catches a deny pattern written against the command line itself.
        executor = Executor(Config(deny_patterns=[r"\blaunch webapp\b"]))
        with mock.patch.object(executor, "_dispatch_lua", return_value=Result(True, "ok")), \
             mock.patch.object(executor, "_query_json", return_value=[]), \
             mock.patch.object(executor, "_query_rows", return_value=([], None)), \
             mock.patch.object(executor, "_shell") as shell:
            result = executor.call("compose_windows", {
                "panes": [{"kind": "web", "target": "https://apnews.com", "name": "AP News"},
                          {"kind": "web", "target": "https://reuters.com", "name": "Reuters"}],
                "workspace": "current"})
        self.assertFalse(result.ok)
        self.assertIn("policy", result.output)
        shell.assert_not_called()


class SinglePaneTests(unittest.TestCase):
    """A layout tool asked to lay out one window means the request was a
    question, not a workspace. The model kept coming here for "who won the
    race" because this was the habitual route to anything on the web."""

    def setUp(self):
        self.executor = Executor(Config())

    def test_one_pane_is_refused_and_names_the_right_tool(self):
        result = self.executor.call("compose_windows", {
            "panes": [{"kind": "web", "target": "https://f1.com", "name": "F1"}]})
        self.assertFalse(result.ok)
        self.assertIn("web_search", result.output)
        self.assertIn("open_page", result.output)

    def test_the_refusal_carries_the_url_so_open_page_is_one_step_away(self):
        result = self.executor.call("compose_windows", {
            "panes": [{"kind": "web", "target": "https://f1.com", "name": "F1"}]})
        self.assertIn("https://f1.com", result.output)

    def test_two_panes_are_still_a_composition(self):
        with mock.patch.object(self.executor, "_query_json", return_value=[]), \
             mock.patch.object(self.executor, "_query_rows", return_value=([], None)), \
             mock.patch.object(self.executor, "_dispatch_lua", return_value=Result(True, "ok")), \
             mock.patch.object(self.executor, "_shell", return_value=Result(True, "started")), \
             mock.patch.object(self.executor, "_await_new_window", return_value=None):
            result = self.executor.call("compose_windows", {
                "panes": [{"kind": "web", "target": "https://a.test", "name": "A"},
                          {"kind": "web", "target": "https://b.test", "name": "B"}],
                "workspace": "current"})
        self.assertTrue(result.ok)


class ComposeRunTests(unittest.TestCase):
    def setUp(self):
        self.config = Config()

    def test_a_pane_whose_window_never_appears_is_reported_not_claimed(self):
        executor = Executor(self.config)
        with mock.patch.object(executor, "_query_json", return_value=[]), \
             mock.patch.object(executor, "_query_rows", return_value=([], None)), \
             mock.patch.object(executor, "_dispatch_lua", return_value=Result(True, "ok")), \
             mock.patch.object(executor, "_shell", return_value=Result(True, "started")), \
             mock.patch.object(executor, "_await_new_window", return_value=None), \
             mock.patch.object(executor, "_terminal_pane_hint",
                               return_value="org.omarchy.voice-terminal"):
            result = executor.call("compose_windows", {
                "panes": [{"kind": "terminal", "target": "", "name": "shell"},
                          {"kind": "terminal", "target": "", "name": "logs"}],
                "workspace": "current"})
        self.assertTrue(result.ok)
        self.assertIn("shell", result.output)
        self.assertIn("did not appear", result.output)

    def test_next_workspace_skips_the_ones_in_use(self):
        executor = Executor(self.config)
        with mock.patch.object(executor, "_query_json", return_value=[
                {"id": 1, "windows": 2}, {"id": 2, "windows": 1}]), \
             mock.patch.object(executor, "_query_rows", return_value=([
                {"id": 1, "windows": 2}, {"id": 2, "windows": 1}], None)):
            self.assertEqual(executor._target_workspace("next"), ("3", ""))

    def test_current_workspace_means_do_not_switch(self):
        self.assertEqual(Executor(self.config)._target_workspace("current"), (None, ""))

    def test_a_nonsense_workspace_is_refused(self):
        target, error = Executor(self.config)._target_workspace("over there")
        self.assertIsNone(target)
        self.assertTrue(error)


DISCORD ={"address": "0xdiscord", "class": "discord", "title": "Discord",
           "focusHistoryID": 0, "workspace": {"name": "1"}}
EDITOR = {"address": "0xeditor", "class": "code", "title": "notes.md",
          "focusHistoryID": 2, "workspace": {"name": "1"}}


def moving_clock():
    """A clock that advances a second per look, so a 10s wait takes no time.

    `_await_new_window` runs against PANE_TIMEOUT and WEB_WINDOW_TIMEOUT, and
    the latter is bound as a default argument, so patching the constant does
    nothing. A suite that waits real seconds is a bug.
    """
    return mock.patch("omarchy_voice.tools.time.monotonic",
                      side_effect=itertools.count(0.0, 1.0))


class ComposeFakes:
    """The desktop as compose sees it: windows, launches and dispatches, faked."""

    def setUp(self):
        self.apps = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.apps)
        self.windows = [EDITOR]
        self.appear: dict[str, list[dict]] = {}   # launched entry -> what maps
        self.lua: list[str] = []
        self.launched: list[list[str]] = []
        # Entries come from here, never from the machine running the tests.
        # Both modules import app_dirs by name, so both are patched (#97).
        for patch in (mock.patch("omarchy_voice.tools.app_dirs", return_value=[self.apps]),
                      mock.patch("omarchy_voice.capabilities.app_dirs",
                                 return_value=[self.apps]),
                      mock.patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": "Hyprland"}),
                      mock.patch("omarchy_voice.tools.shutil.which",
                                 return_value="/run/current-system/sw/bin/gtk-launch")):
            patch.start()
            self.addCleanup(patch.stop)
        clock = moving_clock()
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.use(Config())

    def use(self, config):
        """A fresh executor on the same fakes, e.g. to try another Config."""
        ex = self.executor = Executor(config)
        ex._wait_tick = lambda *a, **k: None
        ex._query_rows = lambda kind: (list(self.windows) if kind == "clients" else [], None)
        ex._query_json = lambda kind: ex._query_rows(kind)[0]
        ex._dispatch_lua = lambda lua: (self.lua.append(lua), Result(True, "ok"))[1]

        def launch(argv, **kwargs):
            # Keyed on the last word, or, for a bare terminal whose last word
            # is a flag, on "omarchy launch terminal".
            self.launched.append(list(argv))
            key = argv[-1] if argv[-1] in self.appear else " ".join(argv[:3])
            self.windows.extend(self.appear.pop(key, []))
            return Result(True, "started")
        ex._shell = launch

    def entry(self, app_id, wm_class="", name="x"):
        (self.apps / f"{app_id}.desktop").write_text(
            f"[Desktop Entry]\nType=Application\nName={name}\nExec=x\n"
            + (f"StartupWMClass={wm_class}\n" if wm_class else ""))


class StrangerWindowTests(ComposeFakes, unittest.TestCase):
    """#75: the window that turned up was not the one launched.

    `_await_new_window` fell back to "any new classed window" when its hint
    never matched. So a Spotify pane that never mapped adopted the Discord
    window that happened to open meanwhile, and moved it onto the composed
    workspace as if it were Spotify.
    """

    def compose(self, target):
        # Two panes, because one is refused (SinglePaneTests). VLC never maps
        # anything, so it only ever lands in "still opening". Installed, as
        # a pane for a missing entry is refused up front (#97).
        self.entry("vlc")
        return self.executor.call("compose_windows", {
            "panes": [{"kind": "app", "target": target, "name": "Chat"},
                      {"kind": "app", "target": "vlc", "name": "VLC"}],
            "workspace": "4"})

    def test_an_unrelated_window_is_not_returned_as_the_one_launched(self):
        """The shared function itself: a hint that never matched gets None."""
        self.windows.append(DISCORD)
        # 5s, not 0.5: the clock steps a second per look, so a shorter wait
        # would never look at all and pass for the wrong reason.
        self.assertIsNone(self.executor._await_new_window(
            {"0xeditor"}, 5.0, "apnews.com"))

    def test_the_declared_class_is_read_from_the_entry(self):
        """Only the [Desktop Entry] group's key counts, and no key or no entry
        is "", which the hint tuple then drops."""
        self.entry("org.telegram.desktop", "TelegramDesktop")
        (self.apps / "odd.desktop").write_text(
            "[Desktop Entry]\nName=x\n[Desktop Action new]\nStartupWMClass=Wrong\n")
        self.entry("plain")
        self.assertEqual(_desktop_wm_class("org.telegram.desktop"), "TelegramDesktop")
        self.assertEqual(_desktop_wm_class("odd"), "")
        self.assertEqual(_desktop_wm_class("plain"), "")
        self.assertEqual(_desktop_wm_class("not-installed"), "")

    def test_hint_tuple_edges(self):
        """An app pane passes (id, declared class), and a missing class is "".
        That must not widen a real hint to "any window", and all-empty
        matches nothing, as "" does (#87)."""
        self.windows.append(DISCORD)
        self.assertIsNone(self.executor._await_new_window({"0xeditor"}, 5.0, ("", "")))
        self.assertIsNone(self.executor._await_new_window(
            {"0xeditor"}, 5.0, ("apnews.com", "")))

    def test_compose_does_not_adopt_an_unrelated_discord_window(self):
        """Spotify never maps and Discord does: Discord stays where it is, and
        the summary names it rather than claiming Spotify composed."""
        self.entry("spotify")
        self.appear["spotify.desktop"] = [DISCORD]
        result = self.compose("spotify")
        self.assertTrue(result.ok, result.output)
        self.assertEqual([l for l in self.lua if "0xdiscord" in l], [])
        self.assertNotIn("Composed", result.output)
        self.assertIn("Did not appear as asked", result.output)
        self.assertIn("discord", result.output)
        self.assertIn("address:0xdiscord", result.output)

    def test_telegram_composes_on_its_declared_class(self):
        """Telegram's window class is TelegramDesktop, which its desktop id
        never matches. Without the declared class the pane is unmatched, and
        before #75 it took Discord, the focused one, instead."""
        self.entry("org.telegram.desktop", "TelegramDesktop")
        telegram = {"address": "0xtelegram", "class": "TelegramDesktop",
                    "title": "Telegram", "focusHistoryID": 1, "workspace": {"name": "1"}}
        self.appear["org.telegram.desktop.desktop"] = [telegram, DISCORD]
        # With the suffix: _pane_command strips one ".desktop", and this id
        # ends in another.
        result = self.compose("org.telegram.desktop.desktop")
        self.assertIn("Composed workspace 4 in a columns layout: Chat.", result.output)
        self.assertTrue(any("0xtelegram" in l and "window.move" in l and '"4"' in l
                            for l in self.lua), self.lua)
        self.assertEqual([l for l in self.lua if "0xdiscord" in l], [])

    def test_telegram_composes_by_its_id(self):
        """The id itself ends in ".desktop", and that suffix is not stripped (#88)."""
        self.entry("org.telegram.desktop", "TelegramDesktop")
        telegram = {"address": "0xtelegram", "class": "TelegramDesktop",
                    "title": "Telegram", "focusHistoryID": 1, "workspace": {"name": "1"}}
        self.appear["org.telegram.desktop.desktop"] = [telegram, DISCORD]
        result = self.compose("org.telegram.desktop")
        self.assertIn("Composed workspace 4 in a columns layout: Chat.", result.output)
        self.assertTrue(any("0xtelegram" in l and "window.move" in l and '"4"' in l
                            for l in self.lua), self.lua)
        self.assertEqual([l for l in self.lua if "0xdiscord" in l], [])

    def test_an_ordinary_id_composes(self):
        self.entry("google-chrome", "Google-chrome")
        chrome = {"address": "0xchrome", "class": "Google-chrome",
                  "title": "Chrome", "focusHistoryID": 1, "workspace": {"name": "1"}}
        self.appear["google-chrome.desktop"] = [chrome, DISCORD]
        result = self.compose("google-chrome")
        self.assertIn("Composed workspace 4 in a columns layout: Chat.", result.output)
        self.assertTrue(any("0xchrome" in l and "window.move" in l for l in self.lua),
                        self.lua)

    def test_the_literal_id_wins_when_both_exist(self):
        """foo and foo.desktop both installed: "foo.desktop" is the id foo.desktop."""
        self.entry("foo")
        self.entry("foo.desktop")
        self.assertEqual(_pane_hint("app", "foo.desktop", ""), "foo.desktop")
        self.assertEqual(_pane_command("app", "foo.desktop", "")[-1], "foo.desktop.desktop")

    def test_a_pwa_still_composes_on_its_desktop_id(self):
        """The pair to Telegram. A Chrome PWA declares a crx_ class it never
        maps with, so it only ever matches on the id: the class must be an
        either-or with the id, not a replacement for it."""
        app = "chrome-dkfoldflcfkbhibhiajfgobmfkifgbdl-Default"
        self.entry(app, "crx_dkfoldflcfkbhibhiajfgobmfkifgbdl")
        sonarr = {"address": "0xsonarr", "class": app, "title": "Sonarr",
                  "focusHistoryID": 1, "workspace": {"name": "1"}}
        self.appear[f"{app}.desktop"] = [sonarr, DISCORD]
        result = self.compose(app)
        self.assertIn("Composed workspace 4 in a columns layout: Chat.", result.output)
        self.assertTrue(any("0xsonarr" in l and "window.move" in l for l in self.lua),
                        self.lua)
        self.assertEqual([l for l in self.lua if "0xdiscord" in l], [])



class ComposeAppPaneTests(ComposeFakes, unittest.TestCase):
    """#97: an app pane is resolved and checked the way launch_app is, before
    anything runs."""

    def setUp(self):
        super().setUp()
        self.entry("dev.zed.Zed", name="Zed")
        self.entry("code", name="Visual Studio Code")
        self.entry("discord", name="Discord")
        self.entry("discord-canary", name="Discord Canary")
        self.entry("vlc", name="VLC")

    def test_launch_app_texts_are_unchanged(self):
        """compose shares these; launch_app's own wording must not move."""
        result = self.executor.call("launch_app", {"app": "nosuch-app"})
        self.assertFalse(result.ok)
        self.assertEqual(
            result.output,
            "no desktop entry named 'nosuch-app' on this system. find_app looks "
            "up what is installed by name or purpose. If this app is in "
            "the manifest's \"Apps this desktop already knows how to open\" "
            "list, call omarchy_cli with the exact command shown there.")
        result = self.executor.call("launch_app", {"app": "Discord"})
        self.assertFalse(result.ok)
        self.assertTrue(result.output.endswith(
            "Ask which, or call launch_app with the id."), result.output)

    GTK = "/run/current-system/sw/bin/gtk-launch"

    def compose(self, first, **extra):
        """Two panes on workspace 4, pane 1 unnamed, pane 2 VLC."""
        self.panes = [{"kind": "app", "target": first, **extra},
                      {"kind": "app", "target": "vlc", "name": "VLC"}]
        return self.executor.call("compose_windows",
                                  {"panes": self.panes, "workspace": "4"})

    def test_a_name_opens_the_entry_it_means(self):
        self.compose("zed")
        self.assertIn([self.GTK, "dev.zed.Zed.desktop"], self.launched)
        self.assertNotIn([self.GTK, "zed.desktop"], self.launched)
        self.assertIn("RESOLVE 'zed' → dev.zed.Zed", self.executor.transcript)
        self.assertEqual(self.panes[0]["target"], "zed")   # the caller's, untouched

    def test_a_missing_app_refuses_the_composition_up_front(self):
        """Refused before the workspace switch and before any wait: the missing
        pane used to cost 12 of the 32 s budget."""
        result = self.compose("nosuch-app")
        self.assertFalse(result.ok)
        self.assertTrue(result.output.startswith(
            "pane 1: no desktop entry named 'nosuch-app'"), result.output)
        self.assertEqual(self.launched, [])
        self.assertEqual(self.lua, [])
        self.assertEqual(self.clock.call_count, 0)

    def test_an_ambiguous_app_lists_both_ids(self):
        result = self.compose("Discord")
        self.assertFalse(result.ok)
        self.assertTrue(result.output.startswith("pane 1: more than one app fits"),
                        result.output)
        self.assertIn("(discord)", result.output)
        self.assertIn("(discord-canary)", result.output)
        self.assertIn("give the pane the id", result.output)
        self.assertEqual(self.launched, [])
        self.assertFalse(any(l.startswith("RUN") for l in self.executor.transcript),
                         self.executor.transcript)

    def test_a_spaced_name_resolves(self):
        self.compose("VS Code")
        self.assertIn([self.GTK, "code.desktop"], self.launched)
        # A name that resolves to nothing is a missing entry, not a bad shape...
        result = self.compose("VS Codez")
        self.assertFalse(result.ok)
        self.assertIn("pane 1: no desktop entry named 'VS Codez'", result.output)
        # ...and only what is not name-shaped keeps the shape refusal. (Not
        # "rm -rf /": the default deny list refuses that before any shape check.)
        result = self.compose("chromium --user-data-dir=/tmp")
        self.assertFalse(result.ok)
        self.assertIn("is not usable as a app target", result.output)

    def test_a_deny_rule_on_the_resolved_id_refuses_the_pane(self):
        self.use(Config(deny_patterns=[r"dev\.zed\.Zed"]))
        result = self.compose("zed")
        self.assertFalse(result.ok)
        self.assertTrue("refused" in result.output
                        or "not allowed by policy" in result.output, result.output)
        self.assertEqual(self.launched, [])

    def test_dry_run_refuses_missing_and_resolves_names(self):
        self.use(Config(dry_run=True))
        result = self.compose("nosuch-app")
        self.assertFalse(result.ok)
        self.assertIn("pane 1: no desktop entry", result.output)
        result = self.compose("zed")
        self.assertTrue(result.ok, result.output)
        self.assertIn("dev.zed.Zed", result.output)   # the label is the id (#97 A)
        self.assertEqual(self.launched, [])


class OneRuleEveryLaunchTests(ComposeFakes, unittest.TestCase):
    """#110: one deny rule on an app stops it through launch_app and through a
    compose `app` pane. Both are checked as `launch <id>`, the id as launched."""

    GTK = "/run/current-system/sw/bin/gtk-launch"
    ZED = r"^launch dev\.zed\.Zed"

    def setUp(self):
        super().setUp()
        self.entry("vlc", name="VLC")
        self.entry("com.ssh.Client", name="Client")
        (self.apps / "dev.zed.Zed.desktop").write_text(
            "[Desktop Entry]\nType=Application\nName=Zed\nExec=x\n"
            "Actions=new-window;\n\n"
            "[Desktop Action new-window]\nName=New Window\nExec=x --new\n")

    def compose(self, target, name=None, second=None):
        """Two panes on workspace 4: the one under test, then VLC."""
        first = {"kind": "app", "target": target, **({"name": name} if name else {})}
        panes = [first, second or {"kind": "app", "target": "vlc", "name": "VLC"}]
        return self.executor.call("compose_windows", {"panes": panes, "workspace": "4"})

    def direct(self):
        """The handler alone, no front gate, on an already resolved pane."""
        return self.executor._tool_compose_windows(
            [{"kind": "app", "target": "dev.zed.Zed", "name": "Zed"},
             {"kind": "app", "target": "vlc", "name": "VLC"}], workspace="4")

    def denied(self):
        return [l for l in self.executor.transcript if l.startswith("DENIED")]

    def assertRefused(self, result):
        self.assertFalse(result.ok, result.output)
        self.assertEqual(self.launched, [])
        self.assertEqual(self.lua, [])   # not even the workspace switch
        self.assertEqual(len(self.denied()), 1, self.executor.transcript)

    def test_the_launch_text(self):
        for app in (" dev.zed.Zed ", "dev.zed.Zed.desktop", "dev.zed.Zed"):
            with self.subTest(app=app):
                self.assertEqual(_launch_text(app), "launch dev.zed.Zed")
        self.assertEqual(_launch_text(" dev.zed.Zed:new-window"),
                         "launch dev.zed.Zed:new-window")
        self.entry("org.telegram.desktop")
        self.assertEqual(_launch_text("org.telegram.desktop"), "launch org.telegram.desktop")

    # -- launch_app ---------------------------------------------------------
    def test_launch_app_with_a_leading_space_is_refused(self):
        self.use(Config(deny_patterns=[self.ZED]))
        self.assertRefused(self.executor.call("launch_app", {"app": " dev.zed.Zed"}))

    def test_launch_app_with_the_desktop_suffix_is_refused(self):
        self.use(Config(deny_patterns=[r"^launch dev\.zed\.Zed$"]))
        self.assertRefused(self.executor.call("launch_app", {"app": "dev.zed.Zed.desktop"}))

    def test_launch_app_with_a_leading_space_and_an_action_is_refused(self):
        self.use(Config(deny_patterns=[r"^launch dev\.zed\.Zed:new-window$"]))
        self.assertRefused(self.executor.call(
            "launch_app", {"app": " dev.zed.Zed:new-window"}))

    def test_launch_app_by_id_and_by_name_is_refused(self):
        for app in ("dev.zed.Zed", "zed"):
            with self.subTest(app=app):
                self.launched.clear()
                self.use(Config(deny_patterns=[self.ZED]))
                self.assertRefused(self.executor.call("launch_app", {"app": app}))

    # -- compose, at the front ---------------------------------------------
    def test_compose_is_refused_at_the_front(self):
        for target, name in (("zed", "Zed"), ("zed", None), (" dev.zed.Zed", None),
                             ("dev.zed.Zed.desktop", None)):
            with self.subTest(target=target, name=name):
                # Cleared first, so one failing subtest does not fail the next.
                self.launched.clear()
                self.lua.clear()
                self.use(Config(deny_patterns=[self.ZED]))
                result = self.compose(target, name)
                self.assertRefused(result)
                self.assertIn("dev.zed.Zed", self.denied()[0])
                self.assertIn(r"blocked by deny rule /^launch dev\.zed\.Zed/", result.output)

    def test_compose_with_the_desktop_suffix_is_refused_under_an_anchored_rule(self):
        """ZED has no `$`, so it matches `launch dev.zed.Zed.desktop` either
        way; only an anchored rule proves the pane's suffix comes off."""
        self.use(Config(deny_patterns=[r"^launch dev\.zed\.Zed$"]))
        self.assertRefused(self.compose("dev.zed.Zed.desktop"))

    # -- deny before confirm, across every text (#110 review) ----------------
    SHUTDOWN = r"^launch org\.gnome\.shutdown$"

    def test_a_deny_on_the_launch_text_beats_a_confirm_on_the_description(self):
        """`launch org.gnome.shutdown.desktop` matches the built-in `shutdown`
        confirm rule; held, the yes would have launched what the deny names."""
        self.entry("org.gnome.shutdown")
        self.use(Config(deny_patterns=[self.SHUTDOWN]))
        self.assertRefused(self.executor.call(
            "launch_app", {"app": "org.gnome.shutdown.desktop"}))
        self.assertIsNone(self.executor.pending)

    def test_a_deny_on_a_pane_beats_a_confirm_at_the_front(self):
        self.entry("org.gnome.shutdown")
        self.use(Config(deny_patterns=[self.SHUTDOWN]))
        self.assertRefused(self.compose("org.gnome.shutdown.desktop"))
        self.assertIsNone(self.executor.pending)

    def test_a_deny_on_a_pane_beats_a_confirm_on_release(self):
        """The handler alone, on the yes: the pane's argv matches the confirm
        rule, which a release lets pass, but its launch text is denied."""
        self.entry("org.gnome.shutdown")
        self.use(Config(deny_patterns=[self.SHUTDOWN]))
        self.executor.pending = ("compose_windows", {
            "panes": [{"kind": "app", "target": "org.gnome.shutdown.desktop",
                       "name": "Off"},
                      {"kind": "app", "target": "vlc", "name": "VLC"}],
            "workspace": "4"})
        result = self.executor.run_pending()
        self.assertFalse(result.ok)
        self.assertEqual(result.output, "pane 1 (Off) is not allowed by policy")
        self.assertEqual(self.launched, [])

    def test_the_same_rule_refuses_launch_app_and_the_pane(self):
        self.use(Config(deny_patterns=[self.ZED]))
        self.assertFalse(self.executor.call("launch_app", {"app": "zed"}).ok)
        self.assertFalse(self.compose("zed").ok)
        self.assertEqual(self.launched, [])
        self.assertEqual(self.lua, [])
        self.assertEqual(len(self.denied()), 2, self.executor.transcript)

    # -- compose, per pane ---------------------------------------------------
    def test_the_pane_check_refuses_without_the_front_gate(self):
        self.use(Config(deny_patterns=[self.ZED]))
        result = self.direct()
        self.assertFalse(result.ok)
        self.assertEqual(result.output, "pane 1 (Zed) is not allowed by policy")
        self.assertEqual(self.launched, [])

    def test_a_confirm_rule_holds_the_compose_and_the_yes_launches_both(self):
        self.use(Config(confirm_patterns=[self.ZED]))
        result = self.compose("zed")
        self.assertEqual(result.output, self.executor.confirm_instruction)
        self.assertEqual(self.executor.pending[0], "compose_windows")
        self.assertEqual(self.launched, [])
        self.executor.run_pending()
        self.assertEqual(self.launched,
                         [[self.GTK, "dev.zed.Zed.desktop"], [self.GTK, "vlc.desktop"]])

    def test_a_confirm_rule_refuses_the_pane_outside_a_release(self):
        self.use(Config(confirm_patterns=[self.ZED]))
        result = self.direct()
        self.assertFalse(result.ok)
        self.assertEqual(result.output, "pane 1 (Zed) is not allowed by policy")
        self.assertEqual(self.launched, [])

    # -- guards --------------------------------------------------------------
    def test_an_action_is_kept(self):
        self.use(Config(deny_patterns=[r"^launch dev\.zed\.Zed$"]))
        result = self.executor.call("launch_app", {"app": "dev.zed.Zed:new-window"})
        self.assertTrue(result.ok, result.output)
        self.assertEqual(self.denied(), [])
        self.assertEqual(self.launched, [[self.GTK, "dev.zed.Zed.desktop:new-window"]])

    def test_the_raw_description_is_still_checked(self):
        self.use(Config(deny_patterns=[r"^launch {2}"]))
        self.assertRefused(self.executor.call("launch_app", {"app": " dev.zed.Zed"}))

    def test_a_url_launch_is_unchanged(self):
        self.use(Config(deny_patterns=[r"^launch firefox$"]))
        result = self.executor.call("launch_app", {"app": "firefox", "url": "https://x.com/"})
        self.assertTrue(result.ok, result.output)
        self.assertEqual(self.launched, [["xdg-open", "https://x.com/"]])

    def test_a_web_pane_is_unchanged(self):
        self.use(Config(deny_patterns=[r"^launch https?://"]))
        result = self.compose("vlc", "VLC", second={"kind": "web", "target": "https://x.com/"})
        self.assertTrue(result.ok, result.output)
        self.assertIn(["omarchy", "launch", "webapp", "https://x.com/"], self.launched)
        self.assertEqual(self.denied(), [])

    def test_the_launcher_argv_still_bites(self):
        self.use(Config(deny_patterns=["gtk-launch"]))
        result = self.compose("zed", "Zed")
        self.assertFalse(result.ok)
        self.assertEqual(result.output, "pane 1 (Zed) is not allowed by policy")
        self.assertEqual(self.launched, [])

    def test_the_built_in_rules_and_their_names(self):
        self.assertTrue(self.executor.call("launch_app", {"app": "zed"}).ok)
        self.compose("zed")
        self.assertEqual(self.launched, [[self.GTK, "dev.zed.Zed.desktop"],
                                         [self.GTK, "dev.zed.Zed.desktop"],
                                         [self.GTK, "vlc.desktop"]])
        result = self.executor.call("launch_app", {"app": "com.ssh.Client"})
        self.assertFalse(result.ok)
        self.assertIn("blocked by deny rule `ssh`", result.output)
        toml = self.apps / "config.toml"
        toml.write_text('[hands]\ndeny_patterns_remove = ["ssh"]\n')
        self.use(config_mod.load(toml))
        self.launched.clear()
        result = self.executor.call("launch_app", {"app": "com.ssh.Client"})
        self.assertTrue(result.ok, result.output)
        self.assertEqual(self.launched, [[self.GTK, "com.ssh.Client.desktop"]])

    TUI = {"kind": "tui", "target": "bash -c 'echo x'", "name": "sh"}

    def test_shell_off_a_deny_comes_before_the_hold(self):
        self.use(Config(allow_shell=False, deny_patterns=[self.ZED]))
        result = self.compose("zed", second=self.TUI)
        self.assertFalse(result.ok)
        self.assertIn("refused", result.output)
        self.assertIsNone(self.executor.pending)
        self.assertEqual(self.launched, [])

    def test_shell_off_without_the_rule_holds_then_launches_both(self):
        self.use(Config(allow_shell=False))
        self.compose("zed", second=self.TUI)
        self.assertIsNotNone(self.executor.pending)
        holds = [l for l in self.executor.transcript if l.startswith("HOLD")]
        self.assertEqual(len(holds), 1)
        self.assertIn("(a compose pane runs a command; allow_shell is off)", holds[0])
        self.executor.run_pending()
        self.assertEqual(len(self.launched), 2, self.launched)
        self.assertEqual(self.launched[0], [self.GTK, "dev.zed.Zed.desktop"])
        self.assertEqual(self.launched[1][:3], ["omarchy", "launch", "tui"])


USERFOOT = {"address": "0xuserfoot", "class": "foot", "initialClass": "foot",
            "title": "~", "initialTitle": "foot", "focusHistoryID": 0,
            "workspace": {"name": "1"}}
HERDR = {"address": "0xherdr", "class": "org.omarchy.herdr", "initialClass": "org.omarchy.herdr",
         "title": "herdr", "initialTitle": "foot", "focusHistoryID": 1,
         "workspace": {"name": "1"}}
AP = {"address": "0xap", "class": "chrome-apnews.com__-Default",
      "initialClass": "chrome-apnews.com__-Default", "title": "AP News",
      "initialTitle": "apnews.com", "focusHistoryID": 0, "workspace": {"name": "1"}}
PANE = {"address": "0xpane", "class": "org.omarchy.voice-terminal",
        "initialClass": "org.omarchy.voice-terminal", "title": "~",
        "initialTitle": "foot", "focusHistoryID": 1, "workspace": {"name": "1"}}


class TerminalPaneTests(ComposeFakes, unittest.TestCase):
    """#87: a terminal pane's hint was "", which took any new classed window.

    So a terminal that was slow to map adopted Discord, the user's own foot or
    an Omarchy TUI, whichever mapped first, and moved it onto the composed
    workspace. The pane is now launched with its own app id and matches only
    that, or the terminal's name when the terminal cannot take the id.
    """

    def setUp(self):
        super().setUp()
        # The read-only xdg-terminal-exec check, faked; the tests never run it.
        self.executor._terminal_pane_hint = lambda: "org.omarchy.voice-terminal"

    def compose(self):
        self.appear["https://apnews.com"] = [AP]
        return self.executor.call("compose_windows", {
            "panes": [{"kind": "terminal", "target": "", "name": "shell"},
                      {"kind": "web", "target": "https://apnews.com", "name": "AP"}],
            "workspace": "4"})

    def dispatched(self, address):
        return [l for l in self.lua if address in l]

    def test_a_terminal_pane_does_not_adopt_discord(self):
        """The terminal never maps and Discord does: Discord stays put and is
        named, and the AP pane still composes."""
        self.appear["omarchy launch terminal"] = [DISCORD]
        result = self.compose()
        self.assertEqual(self.dispatched("0xdiscord"), [])
        self.assertNotIn("Composed workspace 4 in a columns layout: shell", result.output)
        self.assertIn("Did not appear as asked", result.output)
        self.assertIn("discord", result.output)
        self.assertIn("address:0xdiscord", result.output)
        self.assertIn("Composed workspace 4 in a columns layout: AP.", result.output)
        self.assertTrue(any("0xap" in l and "window.move" in l and '"4"' in l
                            for l in self.lua), self.lua)

    def test_the_users_foot_and_an_omarchy_tui_are_not_taken(self):
        self.appear["omarchy launch terminal"] = [USERFOOT, HERDR]
        result = self.compose()
        self.assertEqual(self.dispatched("0xuserfoot"), [])
        self.assertEqual(self.dispatched("0xherdr"), [])
        self.assertIn("address:0xuserfoot", result.output)
        self.assertIn("address:0xherdr", result.output)

    def test_the_tagged_terminal_composes(self):
        self.appear["omarchy launch terminal"] = [PANE, DISCORD]
        result = self.compose()
        self.assertEqual(self.dispatched("0xdiscord"), [])
        self.assertIn("Composed workspace 4 in a columns layout: shell, AP.", result.output)
        self.assertTrue(any("0xpane" in l and "window.move" in l and '"4"' in l
                            for l in self.lua), self.lua)

    def test_an_untaggable_terminal_falls_back_to_its_name(self):
        """Alacritty drops --app-id, so the pane matches on its name instead,
        and that still never widens to Discord."""
        self.executor._terminal_pane_hint = lambda: "alacritty"
        alacritty = {"address": "0xalac", "class": "Alacritty", "title": "~",
                     "focusHistoryID": 1, "workspace": {"name": "1"}}
        self.appear["omarchy launch terminal"] = [alacritty]
        result = self.compose()
        self.assertIn("Composed workspace 4 in a columns layout: shell, AP.", result.output)

        self.windows, self.lua = [EDITOR], []
        self.appear["omarchy launch terminal"] = [DISCORD]
        result = self.compose()
        self.assertEqual(self.dispatched("0xdiscord"), [])
        self.assertIn("address:0xdiscord", result.output)

    def test_the_terminal_check(self):
        """xdg-terminal-exec --print-cmd, read-only: the id when the terminal
        kept it, its program name when it did not, "" when the check failed."""
        executor = Executor(Config())   # setUp's executor has the check faked
        cases = {
            "foot": "foot\n--app-id=org.omarchy.voice-terminal\n",
            "kitty": "kitty\n--class\norg.omarchy.voice-terminal\n",
            "ghostty": "ghostty\n--class=org.omarchy.voice-terminal\n",
            "alacritty": "/nix/store/x-alacritty/bin/alacritty\n",
        }
        expected = {"alacritty": "alacritty"}
        for name, stdout in cases.items():
            with self.subTest(name), mock.patch(
                    "omarchy_voice.tools.subprocess.run",
                    return_value=mock.Mock(returncode=0, stdout=stdout)) as run:
                self.assertEqual(executor._terminal_pane_hint(),
                                 expected.get(name, TERMINAL_PANE_ID))
                self.assertEqual(run.call_args.args[0],
                                 ["xdg-terminal-exec", "--print-cmd",
                                  "--app-id=org.omarchy.voice-terminal"])
        failures = {
            "OSError": {"side_effect": OSError("not found")},
            "timeout": {"side_effect": subprocess.TimeoutExpired("xdg-terminal-exec", 4)},
            "exit 1": {"return_value": mock.Mock(returncode=1, stdout="foot\n")},
            "blank": {"return_value": mock.Mock(returncode=0, stdout="\n")},
        }
        for name, kwargs in failures.items():
            with self.subTest(name), mock.patch("omarchy_voice.tools.subprocess.run", **kwargs):
                self.assertEqual(executor._terminal_pane_hint(), "")

    def test_an_empty_hint_matches_nothing(self):
        """A failed terminal check, or a URL with no host, is "": it takes no
        window at all rather than any window."""
        self.windows.append(DISCORD)
        self.assertIsNone(self.executor._await_new_window({"0xeditor"}, 5.0, ""))
        self.assertIsNone(self.executor._await_new_window({"0xeditor"}, 5.0, ("", "")))

        self.windows = [EDITOR]
        self.executor._terminal_pane_hint = lambda: ""
        self.appear["omarchy launch terminal"] = [DISCORD]
        result = self.compose()
        self.assertEqual(self.dispatched("0xdiscord"), [])
        self.assertIn("Did not appear as asked", result.output)
        self.assertIn("address:0xdiscord", result.output)


class CommandLookupTests(unittest.TestCase):
    """The CLI list moved out of the prompt and behind a tool: 128 routes at
    ~2,270 tokens were resent every turn against a per-minute budget."""

    INDEX = [
        ("omarchy theme set <theme-name>", "Switch to a different theme"),
        ("omarchy theme list", "List available themes"),
        ("omarchy toggle nightlight [--status]", "Toggle nightlight screen filter"),
        ("omarchy audio output volume <raise|lower>", "Change the output volume"),
    ]

    def search(self, query, **kw):
        with mock.patch("omarchy_voice.capabilities.command_index",
                        return_value=self.INDEX):
            from omarchy_voice import capabilities
            return capabilities.search_commands(query, **kw)

    def test_a_phrase_with_an_unmatched_word_still_finds_the_route(self):
        # "dark" appears in no route; requiring every word found nothing at all.
        hits = self.search("dark theme")
        self.assertTrue(hits)
        self.assertIn("theme", hits[0])

    def test_a_route_hit_outranks_a_summary_hit(self):
        hits = self.search("volume")
        self.assertIn("audio output volume", hits[0])

    def test_nothing_matches_returns_nothing(self):
        self.assertEqual(self.search("xyzzy"), [])

    def test_an_empty_query_returns_nothing(self):
        self.assertEqual(self.search("   "), [])

    def test_results_are_capped(self):
        self.assertLessEqual(len(self.search("omarchy", limit=2)), 2)

    def test_the_tool_tells_the_model_what_to_do_with_a_hit(self):
        executor = Executor(Config())
        with mock.patch("omarchy_voice.capabilities.search_commands",
                        return_value=["  omarchy theme list"]):
            result = executor.call("omarchy_help", {"query": "theme"})
        self.assertTrue(result.ok)
        self.assertIn("omarchy_cli", result.output)

    def test_a_miss_suggests_a_plainer_word(self):
        executor = Executor(Config())
        with mock.patch("omarchy_voice.capabilities.search_commands", return_value=[]):
            result = executor.call("omarchy_help", {"query": "xyzzy"})
        self.assertFalse(result.ok)
        self.assertIn("plainer", result.output)

    def test_lookup_is_read_only(self):
        from omarchy_voice.tools import READ_ONLY_TOOLS
        self.assertIn("omarchy_help", READ_ONLY_TOOLS)


class ReadScreenTests(unittest.TestCase):
    """OCR is the answer to "what does it say". The assistant used to tell the
    user it could not read the screen at all."""

    def setUp(self):
        self.executor = Executor(Config())

    def test_a_window_on_a_hidden_workspace_is_refused_with_the_fix(self):
        with mock.patch.object(self.executor, "_query_json", side_effect=lambda k: {
            "clients": [{"address": "0xa", "workspace": {"name": "7"},
                         "at": [0, 0], "size": [100, 100]}],
            "monitors": [{"activeWorkspace": {"name": "1"}}],
        }[k]):
            result = self.executor.call("read_screen", {"target": "address:0xa"})
        self.assertFalse(result.ok)
        self.assertIn("workspace 7", result.output)
        # The refusal has to say how to fix it, in a form the model can
        # actually call. It used to name hl.dsp.focus, which #22 stopped
        # accepting -- a refusal whose advice no longer works costs the turn
        # it was meant to save.
        self.assertIn("hypr_dispatch", result.output)
        self.assertIn("focus", result.output)
        self.assertNotIn("hl.dsp", result.output)

    def test_a_visible_window_is_ocred_at_its_geometry(self):
        with mock.patch.object(self.executor, "_query_json", side_effect=lambda k: {
            "clients": [{"address": "0xa", "workspace": {"name": "1"},
                         "at": [12, 38], "size": [800, 600]}],
            "monitors": [{"activeWorkspace": {"name": "1"}}],
        }[k]), mock.patch.object(self.executor, "_ocr_region",
                                 return_value=Result(True, "hello")) as ocr:
            result = self.executor.call("read_screen", {"target": "address:0xa"})
        self.assertTrue(result.ok)
        ocr.assert_called_once_with("12,38 800x600")

    def test_screen_reads_the_focused_monitor(self):
        with mock.patch.object(self.executor, "_query_json", return_value=[
            {"focused": False, "x": 0, "y": 0, "width": 100, "height": 100},
            {"focused": True, "x": 2560, "y": 0, "width": 1920, "height": 1080},
        ]), \
             mock.patch.object(self.executor, "_query_rows", return_value=([
            {"focused": False, "x": 0, "y": 0, "width": 100, "height": 100},
            {"focused": True, "x": 2560, "y": 0, "width": 1920, "height": 1080},
        ], None)), mock.patch.object(self.executor, "_ocr_region",
                              return_value=Result(True, "text")) as ocr:
            self.executor.call("read_screen", {})
        ocr.assert_called_once_with("2560,0 1920x1080")

    def test_a_stale_address_says_to_name_the_window_instead(self):
        """It used to say "call hypr_query(clients) for current addresses".

        That advice cost a model turn and a 485-token JSON dump, for a lookup
        the resolver now does in 13ms (#27). A refusal that sends the model
        round the loop this change exists to remove is worse than no advice.
        """
        clients = [{"address": "0xa", "class": "foot", "title": "shell",
                    "workspace": {"name": "1"}, "at": [0, 0], "size": [10, 10]}]
        with mock.patch.object(self.executor, "_query_json", return_value=clients):
            result = self.executor.call("read_screen", {"target": "address:0xgone"})
        self.assertFalse(result.ok)
        self.assertNotIn("hypr_query", result.output)
        self.assertIn("Name the window", result.output)

    def test_nothing_open_is_not_a_lookup_failure(self):
        with mock.patch.object(self.executor, "_query_json", return_value=[]), \
             mock.patch.object(self.executor, "_query_rows", return_value=([], None)):
            result = self.executor.call("read_screen", {"target": "address:0xgone"})
        self.assertFalse(result.ok)
        self.assertIn("nothing is open", result.output)

    def test_a_screenful_of_text_is_capped(self):
        # _screen_unavailable is stubbed because it asks the real hyprctl through
        # Popen, which subprocess.run does not cover: with the monitor in DPMS
        # off this test used to fail on the sleeping-display refusal instead of
        # on anything to do with the cap.
        # _query_rows likewise: since #51 the capture refuses when the window
        # list cannot be read, and in the sandbox there is no compositor to
        # read it from -- which is the guard working, not this test's subject.
        from omarchy_voice.tools import OCR_LIMIT
        with mock.patch.object(self.executor, "_screen_unavailable", return_value=None), \
             mock.patch.object(self.executor, "_query_rows", return_value=([], None)), \
             mock.patch.object(self.executor, "_screen_is_recorded", return_value=None), \
             mock.patch("subprocess.run") as run:
            run.side_effect = [
                mock.Mock(returncode=0, stdout=b"PNG", stderr=b""),
                mock.Mock(returncode=0, stdout=b"x" * (OCR_LIMIT * 2), stderr=b""),
            ]
            result = self.executor._ocr_region("0,0 100x100")
        self.assertTrue(result.ok)
        self.assertLess(len(result.output), OCR_LIMIT + 200)
        self.assertIn("not read", result.output)

    def test_read_screen_survives_dry_run(self):
        # Read-only, like hypr_query: --dry-run must still be able to look.
        from omarchy_voice.tools import READ_ONLY_TOOLS
        self.assertIn("read_screen", READ_ONLY_TOOLS)


class ClickByTextTests(unittest.TestCase):
    """Clicking by coordinates is useless to someone talking. The request that
    prompted this was "double-click into this US and Iran trade strikes"."""

    # A headline OCR'd into words, with one word misread ("frade").
    WORDS = [
        {"text": "Breaking", "x": 10, "y": 10, "w": 60, "h": 12, "conf": 90},
        {"text": "US", "x": 100, "y": 50, "w": 20, "h": 12, "conf": 95},
        {"text": "and", "x": 130, "y": 50, "w": 25, "h": 12, "conf": 95},
        {"text": "Iran", "x": 160, "y": 50, "w": 30, "h": 12, "conf": 95},
        {"text": "frade", "x": 195, "y": 50, "w": 35, "h": 12, "conf": 60},
        {"text": "strikes", "x": 235, "y": 50, "w": 45, "h": 12, "conf": 92},
        {"text": "Continue", "x": 400, "y": 300, "w": 70, "h": 14, "conf": 98},
    ]

    def setUp(self):
        self.executor = Executor(Config())
        benign = {"address": "0xa", "class": "foot", "title": "shell",
                    "at": [0, 0], "size": [800, 600],
                    "workspace": {"name": "1"}, "focusHistoryID": 0}
        self.executor._query_rows = lambda kind: ([benign], None)
        self.executor._query_json = lambda kind: [benign]

    def test_half_the_words_is_not_a_match(self):
        # "Files changed" used to match the prose "changed files and file tree"
        # elsewhere on the page and click it, confidently, in the wrong place.
        words = [
            {"text": "changed", "x": 10, "y": 400, "w": 50, "h": 12, "conf": 90},
            {"text": "documents", "x": 70, "y": 400, "w": 60, "h": 12, "conf": 90},
        ]
        self.assertIsNone(self.executor._find_phrase(words, "Files changed"))

    def test_a_short_query_needs_every_word(self):
        from omarchy_voice.tools import _required_hits
        self.assertEqual(_required_hits(1), 1)
        self.assertEqual(_required_hits(2), 2)
        self.assertEqual(_required_hits(3), 3)

    def test_a_long_query_tolerates_one_ocr_miss(self):
        from omarchy_voice.tools import _required_hits
        self.assertEqual(_required_hits(5), 4)

    def test_ocr_uses_automatic_page_segmentation(self):
        # psm 6 assumes one uniform block and read straight past the tab bar of
        # a GitHub pull request; psm 3 segments the page and finds it.
        from omarchy_voice.tools import OCR_PAGE_MODE
        self.assertEqual(OCR_PAGE_MODE, 3)

    def test_a_phrase_is_found_despite_a_misread_word(self):
        point = self.executor._find_phrase(self.WORDS, "US and Iran trade strikes")
        self.assertIsNotNone(point)
        x, y = point
        # Centre should land inside the headline's span, not on "Breaking".
        self.assertTrue(100 <= x <= 280, x)
        self.assertTrue(50 <= y <= 62, y)

    def test_a_single_word_button_is_found(self):
        x, y = self.executor._find_phrase(self.WORDS, "Continue")
        self.assertTrue(400 <= x <= 470)
        self.assertTrue(300 <= y <= 314)

    def test_text_that_is_not_there_is_not_invented(self):
        self.assertIsNone(self.executor._find_phrase(self.WORDS, "Delete everything"))

    def test_no_words_no_match(self):
        self.assertIsNone(self.executor._find_phrase([], "Continue"))

    def test_empty_text_is_refused(self):
        self.assertIsNotNone(self.executor._validate_click_text("  "))

    def test_a_bad_button_is_refused(self):
        self.assertIsNotNone(self.executor._validate_click_text("ok", button="scroll"))

    def test_missing_text_reports_and_does_not_click(self):
        with mock.patch.object(self.executor, "_query_json",
                               return_value=[{"focused": True, "x": 0, "y": 0,
                                              "width": 100, "height": 100}]), \
             mock.patch.object(self.executor, "_ocr_words", return_value=(self.WORDS, "")), \
             mock.patch.object(self.executor, "_press_button") as press:
            result = self.executor.call("click_text", {"text": "Nonexistent Button"})
        self.assertFalse(result.ok)
        self.assertIn("could not find", result.output)
        press.assert_not_called()

    def test_a_hit_moves_the_pointer_then_clicks(self):
        with mock.patch.object(self.executor, "_query_json",
                               return_value=[{"focused": True, "x": 0, "y": 0,
                                              "width": 500, "height": 500}]), \
             mock.patch.object(self.executor, "_ocr_words", return_value=(self.WORDS, "")), \
             mock.patch.object(self.executor, "_dispatch_lua",
                               return_value=Result(True, "ok")) as move, \
             mock.patch.object(self.executor, "_press_button",
                               return_value=Result(True, "")) as press:
            result = self.executor.call("click_text", {"text": "Continue", "double": True})
        self.assertTrue(result.ok)
        self.assertIn("cursor.move", move.call_args[0][0])
        press.assert_called_once_with("left", True)

    def test_without_a_helper_it_refuses_rather_than_pretending(self):
        """#30: this used to return a paragraph of ydotool/NixOS advice.

        The advice existed because ydotool needed a root daemon. The Wayland
        helper needs none, so the only reasons left are a missing binary or a
        compositor without zwlr_virtual_pointer_v1 -- and either way the honest
        answer is short and is a failure.
        """
        self.executor.input_helper = None
        result = self.executor._press_button("left", False)
        self.assertFalse(result.ok)
        self.assertNotIn("ydotool", result.output)

    def test_low_confidence_words_are_dropped(self):
        from omarchy_voice.tools import MIN_OCR_CONFIDENCE
        self.assertGreater(MIN_OCR_CONFIDENCE, 0)


class SleepingScreenTests(unittest.TestCase):
    """grim blocks rather than failing when the monitor is in DPMS off, so a
    read at half past midnight hung for the full timeout."""

    def setUp(self):
        self.executor = Executor(Config())
        # #67's input guard needs a desktop to look at; without it these
        # reached the real hyprctl locally and failed in the sandbox.
        benign = {"address": "0xa", "class": "foot", "title": "shell",
                    "at": [0, 0], "size": [800, 600],
                    "workspace": {"name": "1"}, "focusHistoryID": 0}
        self.executor._query_rows = lambda kind: ([benign], None)
        self.executor._query_json = lambda kind: [benign]
        # The lock probe shells out to the real omarchy-shell. These tests are
        # about DPMS, and on a machine whose session happens to be locked they
        # would otherwise all fail on the lock refusal instead.
        locked = mock.patch.object(self.executor, "_session_is_locked",
                                   return_value=False)
        locked.start()
        self.addCleanup(locked.stop)

    def test_a_sleeping_display_is_reported_not_captured(self):
        with mock.patch.object(self.executor, "_query_json",
                               return_value=[{"name": "HDMI-A-1", "dpmsStatus": False}]), \
             mock.patch("subprocess.run") as run:
            result = self.executor._ocr_region("0,0 100x100")
        self.assertFalse(result.ok)
        self.assertIn("asleep", result.output)
        self.assertIn("dpms", result.output)
        run.assert_not_called()

    def test_an_awake_display_captures_normally(self):
        with mock.patch.object(self.executor, "_query_json",
                               return_value=[{"name": "HDMI-A-1", "dpmsStatus": True}]):
            self.assertIsNone(self.executor._screen_unavailable())

    def test_one_awake_monitor_is_enough(self):
        with mock.patch.object(self.executor, "_query_json", return_value=[
                {"dpmsStatus": False}, {"dpmsStatus": True}]), \
             mock.patch.object(self.executor, "_query_rows", return_value=([
                {"dpmsStatus": False}, {"dpmsStatus": True}], None)):
            self.assertIsNone(self.executor._screen_unavailable())

    def test_a_headless_virtual_output_is_not_refused(self):
        """#55: a VM's only monitor reports disabled while rendering fine.

        Measured in a nixarchy guest: dpms=True, disabled=True, and grim
        returned a 1280x800 capture whose OCR matched five lines of the windows
        on it. `disabled` is deliberately not consulted.
        """
        with mock.patch.object(self.executor, "_query_json", return_value=[
                {"name": "Virtual-1", "dpmsStatus": True, "disabled": True}]):
            self.assertIsNone(self.executor._screen_unavailable())

    def test_a_disabled_monitor_that_is_also_asleep_still_refuses(self):
        with mock.patch.object(self.executor, "_query_json", return_value=[
                {"name": "Virtual-1", "dpmsStatus": False, "disabled": True}]):
            self.assertIn("asleep", self.executor._screen_unavailable() or "")

    def test_the_lock_check_wins_over_a_perfectly_healthy_monitor(self):
        """The dangerous case: a lock screen captures SUCCESSFULLY."""
        with mock.patch.object(self.executor, "_session_is_locked", return_value=True), \
             mock.patch.object(self.executor, "_query_json", return_value=[
                 {"name": "HDMI-A-1", "dpmsStatus": True, "disabled": False}]):
            self.assertIn("locked", self.executor._screen_unavailable() or "")

    def test_no_monitors_at_all_still_lets_the_capture_try(self):
        with mock.patch.object(self.executor, "_query_json", return_value=[]):
            self.assertIsNone(self.executor._screen_unavailable())

    def test_clicking_a_sleeping_screen_is_refused(self):
        with mock.patch.object(self.executor, "_query_json", side_effect=lambda k: {
                "monitors": [{"focused": True, "x": 0, "y": 0, "width": 100,
                              "height": 100, "dpmsStatus": False}]}[k]), \
             mock.patch.object(self.executor, "_press_button") as press:
            result = self.executor.call("click_text", {"text": "Continue"})
        self.assertFalse(result.ok)
        self.assertIn("asleep", result.output)
        press.assert_not_called()


class TruncationTests(unittest.TestCase):
    """`hyprctl -j clients` runs ~750 characters per window; the old 4000-char
    cut silently handed the model unparseable JSON from about the fifth."""

    def test_a_long_client_list_survives_the_query(self):
        clients = [{"address": f"0x{i:x}", "class": "foot", "title": "t" * 200,
                    "pid": i, "floating": False, "fullscreen": 0,
                    "workspace": {"name": "1"}} for i in range(20)]
        executor = Executor(Config())
        with mock.patch.object(Executor, "_shell",
                               return_value=Result(True, json.dumps(clients))):
            result = executor.call("hypr_query", {"kind": "clients"})
        self.assertTrue(result.ok)
        parsed = json.loads(result.output)  # must not raise
        self.assertEqual(len(parsed), 20)

    def test_prose_output_is_still_capped_and_says_so(self):
        with mock.patch("subprocess.Popen") as popen:
            popen.return_value.communicate.return_value = ("x" * 9000, "")
            popen.return_value.returncode = 0
            result = Executor(Config(dry_run=False))._shell(["echo"], limit=100)
        self.assertTrue(result.ok)
        self.assertIn("truncated", result.output)


class LoggedMistakeTests(unittest.TestCase):
    """Each of these is a call the assistant actually made in the session log."""

    def test_change_id_used_for_navigation_is_refused(self):
        error = _check_dispatch_args("workspace.change_id", {"id": "5"})[1]
        self.assertIn("focus", error)

    def test_change_id_with_only_a_workspace_is_refused(self):
        self.assertIsNotNone(
            _check_dispatch_args("workspace.change_id", {"workspace": "4"})[1])

    def test_a_genuine_rename_is_allowed(self):
        self.assertIsNone(_check_dispatch_args(
            "workspace.change_id", {"workspace": "2", "id": "7"})[1])

    def test_focus_is_untouched(self):
        self.assertIsNone(_check_dispatch_args("focus", {"workspace": "5"})[1])

    def test_signature_placeholders_are_refused_with_advice(self):
        argv, error = normalise_omarchy(
            "launch-or-focus webapp <window-pattern> x https://x.com/")
        self.assertIn("<window-pattern>", error)
        self.assertIn("placeholder", error)

    def test_a_hyphenated_route_is_repaired(self):
        argv, error = normalise_omarchy("launch-or-focus webapp x https://x.com/")
        self.assertIsNone(error)
        self.assertEqual(argv[:4], ["launch", "or", "focus", "webapp"])

    def test_a_doubled_omarchy_prefix_is_dropped(self):
        argv, error = normalise_omarchy("omarchy omarchy launch terminal")
        self.assertIsNone(error)
        self.assertEqual(argv, ["launch", "terminal"])

    def test_describe_shows_the_command_that_will_run(self):
        self.assertEqual(
            Executor.describe("omarchy_cli", {"command": "launch-or-focus webapp x https://x.com/"}),
            "omarchy launch or focus webapp x https://x.com/")

    def test_a_less_than_sign_in_a_real_argument_is_not_a_placeholder(self):
        argv, error = normalise_omarchy('notification dismiss "<3"')
        self.assertIsNone(error)


if __name__ == "__main__":
    unittest.main()
