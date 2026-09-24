"""Find an installed app by the name a person uses (#70).

The model used to be shown 28 of this machine's 334 apps -- mostly Steam
runtimes -- so "open zed" was a guess: `launch_app "zed"`, then an `omarchy
launch zed` that does not exist. Zed is `dev.zed.Zed`, run as `zeditor`, and
all three facts are in one local file.

Every entry here is written by the test. `app_dirs()` always includes the
user's own applications directory, so patching XDG_DATA_DIRS alone would still
read whatever this machine has installed.

Run with: python3 -m unittest discover -s tests
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omarchy_voice import capabilities, tools
from omarchy_voice.config import Config
from omarchy_voice.tools import READ_ONLY_TOOLS, Executor, tools_for

ENTRIES = {
    "dev.zed.Zed": "Name=Zed\nExec=zeditor %U\n",
    "org.gnome.Nautilus": "Name=Files\nKeywords=folder;manager;\nExec=nautilus\n",
    "preferred-file-manager": "Name=File Manager\nExec=omarchy-launch-files\n",
    "code": "Name=Visual Studio Code\nExec=code %F\n",
    "claude-code": "Name=Claude Code\nExec=claude\n",
    "discord": "Name=Discord\nExec=discord\n",
    "omarchy-Discord": "Name=Discord\nExec=omarchy-launch-webapp discord\n",
    "preferred-web-browser": "Name=Web Browser\nExec=omarchy-launch-browser\n",
    "yad-icon-browser": "Name=Icon Browser\nExec=yad-icon-browser\n",
    "hidden": "Name=Hidden Thing\nNoDisplay=true\nExec=hidden\n",
    "gnome-only": "Name=Gnome Only\nOnlyShowIn=GNOME;\nExec=gnome-only\n",
    "not-here": "Name=Not Here\nNotShowIn=Hyprland;\nExec=not-here\n",
    "with-action": ("Name=Test Browser\nExec=test-browser\nActions=new-window;\n"
                   "\n[Desktop Action new-window]\nName=Decoy\nExec=test-browser -n\n"),
}


class AppCase(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        first, second = root / "first", root / "second"
        first.mkdir()
        second.mkdir()
        for app_id, body in ENTRIES.items():
            (first / f"{app_id}.desktop").write_text(
                f"[Desktop Entry]\nType=Application\n{body}")
        # The same id later in precedence order: the first one wins.
        (second / "dev.zed.Zed.desktop").write_text("[Desktop Entry]\nName=Imposter\n")
        for module in (capabilities, tools):
            patcher = mock.patch.object(module, "app_dirs", return_value=[first, second])
            patcher.start()
            self.addCleanup(patcher.stop)
        env = mock.patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": "Hyprland"})
        env.start()
        self.addCleanup(env.stop)

    def top(self, query):
        found = capabilities.find_apps(query)
        return found[0][1]["id"] if found else None

    def clear(self, query):
        match = capabilities.clear_match(capabilities.find_apps(query))
        return match["id"] if match else None


class IndexTests(AppCase):
    def test_only_what_the_launcher_would_show(self):
        ids = {row["id"] for row in capabilities.app_index()}
        self.assertNotIn("hidden", ids)
        self.assertNotIn("gnome-only", ids)
        self.assertNotIn("not-here", ids)
        self.assertIn("dev.zed.Zed", ids)

    def test_an_action_group_is_not_the_app(self):
        [row] = [r for r in capabilities.app_index() if r["id"] == "with-action"]
        self.assertEqual(row["name"], "Test Browser")
        self.assertEqual(row["actions"], ["new-window"])

    def test_the_first_directory_wins(self):
        [row] = [r for r in capabilities.app_index() if r["id"] == "dev.zed.Zed"]
        self.assertEqual((row["name"], row["command"]), ("Zed", "zeditor"))


class MatchTests(AppCase):
    def test_the_names_people_use(self):
        for said, meant in (("zed", "dev.zed.Zed"), ("open zed", "dev.zed.Zed"),
                            ("zeditor", "dev.zed.Zed"),
                            ("the file manager", "preferred-file-manager"),
                            ("vs code", "code")):
            with self.subTest(said=said):
                self.assertEqual(self.clear(said), meant)

    def test_a_close_spelling_is_found_but_never_launched_unasked(self):
        self.assertEqual(self.top("zedd"), "dev.zed.Zed")
        self.assertIsNone(self.clear("zedd"))

    def test_an_ambiguous_name_is_a_choice(self):
        for said in ("code", "discord"):
            with self.subTest(said=said):
                self.assertIsNone(self.clear(said))

    def test_the_configured_default_comes_first(self):
        self.assertEqual(self.top("browser"), "preferred-web-browser")

    def test_nothing_is_nothing(self):
        self.assertEqual(capabilities.find_apps("flurble"), [])


class LaunchByNameTests(AppCase):
    def launched(self, executor, app):
        with mock.patch.object(tools.shutil, "which", return_value="/bin/uwsm-app"), \
             mock.patch.object(executor, "_shell",
                               return_value=tools.Result(True, "ok")) as shell:
            result = executor.call("launch_app", {"app": app})
        return result, shell

    def test_a_name_launches_the_entry_it_means(self):
        executor = Executor(Config(dry_run=False))
        result, shell = self.launched(executor, "zed")
        self.assertTrue(result.ok)
        self.assertEqual(shell.call_args.args[0], ["/bin/uwsm-app", "dev.zed.Zed.desktop"])
        self.assertIn("RESOLVE 'zed' → dev.zed.Zed", executor.transcript)

    def test_the_gate_judges_the_resolved_id(self):
        """A rule written against an id must catch the name that means it."""
        executor = Executor(Config(dry_run=False, deny_patterns=[r"dev\.zed\.Zed"]))
        result, shell = self.launched(executor, "zed")
        self.assertFalse(result.ok)
        self.assertIn("refused", result.output)
        shell.assert_not_called()

    def test_a_choice_runs_nothing(self):
        executor = Executor(Config(dry_run=False))
        result, shell = self.launched(executor, "discord app")
        self.assertFalse(result.ok)
        self.assertIn("(discord)", result.output)
        self.assertIn("(omarchy-Discord)", result.output)
        shell.assert_not_called()

    def test_a_real_desktop_id_is_taken_as_it_is(self):
        """`code` is an id here; only a name that is not one is looked up."""
        executor = Executor(Config(dry_run=False))
        result, shell = self.launched(executor, "code")
        self.assertTrue(result.ok)
        self.assertEqual(shell.call_args.args[0], ["/bin/uwsm-app", "code.desktop"])

    def test_a_command_line_never_reaches_the_matcher(self):
        executor = Executor(Config(dry_run=False))
        with mock.patch.object(capabilities, "find_apps") as find:
            result = executor.call("launch_app", {"app": "bash -c 'echo hi'"})
        self.assertIn("desktop id", result.output)
        find.assert_not_called()


class FindAppToolTests(AppCase):
    def test_it_lists_what_fits(self):
        result = Executor(Config()).call("find_app", {"query": "manager"})
        self.assertTrue(result.ok)
        self.assertIn("Files (org.gnome.Nautilus)", result.output)

    def test_nothing_installed_is_said_plainly(self):
        result = Executor(Config()).call("find_app", {"query": "flurble"})
        self.assertTrue(result.ok)
        self.assertIn("not installed", result.output)

    def test_it_is_a_read_and_every_engine_gets_it(self):
        self.assertIn("find_app", READ_ONLY_TOOLS)
        # tools_for is what the MCP server, the realtime engine and the planner
        # all build their tool lists from.
        self.assertIn("find_app", [schema["name"] for schema in tools_for(Config())])


class ManifestTests(AppCase):
    def test_the_manifest_lists_no_apps_and_points_at_find_app(self):
        with mock.patch.object(capabilities, "CACHE_DIR", Path(tempfile.mkdtemp())), \
             mock.patch.object(capabilities, "_run", return_value=""):
            text = capabilities.manifest(refresh=True)
        self.assertIn("find_app", text)
        self.assertNotIn("Zed (dev.zed.Zed)", text)
        self.assertNotIn("Visual Studio Code", text)


if __name__ == "__main__":
    unittest.main()
