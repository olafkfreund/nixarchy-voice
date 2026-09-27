"""`omarchy-voice action ...` (#157, plan step 7), in the throwaway HOME.

No editor opens and no systemd call is made: both are replaced.

Run with: python3 -m unittest discover -s tests
"""

import contextlib
import io
import shutil
import unittest
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import actions as act
from omarchy_voice import cli

act._systemctl = lambda *args: (True, "")


def run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch.object(cli.subprocess, "run") as editor, \
            mock.patch.object(cli, "daemon_running", return_value=False):
        code = cli.main(["--dry-run", "action", *argv])
    return code, out.getvalue() + err.getvalue() + str(editor.call_args_list)


class TestActionCli(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(act.ACTIONS_DIR, ignore_errors=True)
        act.MENU_FILE.unlink(missing_ok=True)

    def test_new_from_template_is_valid_and_opens_the_editor(self):
        code, out = run("new", "morning")
        self.assertEqual(code, 0, out)
        self.assertIn("omarchy-launch-editor", out)
        self.assertIn("morning is valid", out)
        self.assertIn("voice.actions.morning.run", act.MENU_FILE.read_text())

    def test_new_refuses_to_overwrite(self):
        run("new", "x")
        self.assertEqual(run("new", "x")[0], 1)

    def test_new_without_a_name(self):
        run("new")
        self.assertTrue(act.path_for("untitled-1").exists())

    def test_list_show_run_delete(self):
        run("new", "demo")
        code, out = run("list")
        self.assertIn("demo", out)
        code, out = run("show", "demo")
        self.assertIn("# An Oma action", out)  # the file as written, comments kept
        # dry-run: the tool step narrates; the ask step needs a model, so the
        # run is stubbed at the brain
        fake = mock.Mock()
        fake.return_value.think.return_value = mock.Mock(error=None, reply="all quiet")
        fake.return_value.pending = None
        with mock.patch.object(cli, "choose_backend", return_value=(fake, "")), \
                mock.patch.object(cli, "_report") as report:
            code, out = run("run", "demo", "--unattended")
        self.assertEqual(code, 0, out)
        self.assertIn("1. [dry-run] would run", out)
        self.assertIn("2. all quiet", out)
        report.assert_called_once()
        code, out = run("delete", "demo")
        self.assertEqual(code, 0, out)
        self.assertFalse(act.path_for("demo").exists())

    def test_unattended_hold_is_reported_not_run(self):
        act.ACTIONS_DIR.mkdir(parents=True, exist_ok=True)
        act.path_for("t").write_text('[[step]]\ntool = "run_in_terminal"\n'
                                     'args = { command = "herdr" }\n')
        with mock.patch.object(cli, "_report"):
            code, out = run("run", "t", "--unattended")
        self.assertEqual(code, 1)
        self.assertIn("needs your approval", out)
        self.assertIn("omarchy-voice action approve t", out)

    def test_enable_keeps_comments(self):
        run("new", "r")
        path = act.path_for("r")
        path.write_text(path.read_text().replace("# [schedule]", "[schedule]")
                        .replace('# when = "Mon', 'when = "Mon')
                        .replace("# enabled = true", "enabled = false"))
        code, out = run("enable", "r")
        self.assertEqual(code, 0, out)
        text = path.read_text()
        self.assertIn("enabled = true", text)
        self.assertIn("# An Oma action", text)
        code, out = run("disable", "r")
        self.assertIn("enabled = false", path.read_text())

    def test_enable_without_a_schedule(self):
        run("new", "plain")
        code, out = run("enable", "plain")
        self.assertEqual(code, 1)
        self.assertIn("no [schedule]", out)

    def test_enable_a_declared_routine(self):
        # Home Manager installs a declared action as a link into the store.
        target = act.ACTIONS_DIR.parent / "declared.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('[[step]]\nask = "x"\n\n[schedule]\nwhen = "daily"\n')
        act.ACTIONS_DIR.mkdir(parents=True, exist_ok=True)
        act.path_for("decl").symlink_to(target)
        try:
            code, out = run("disable", "decl")
        finally:
            target.unlink()
        self.assertEqual(code, 1)
        self.assertIn("declared in Home Manager", out)
        self.assertNotIn("Traceback", out)

    def test_a_hand_written_file_reaches_the_menu_on_list(self):
        act.ACTIONS_DIR.mkdir(parents=True, exist_ok=True)
        act.path_for("byhand").write_text('[[step]]\nask = "x"\n')
        self.assertFalse(act.MENU_FILE.exists())
        run("list")
        self.assertIn("voice.actions.byhand.run", act.MENU_FILE.read_text())

    def test_broken_file_is_named(self):
        act.ACTIONS_DIR.mkdir(parents=True, exist_ok=True)
        act.path_for("bad").write_text('[[step]]\ntool = "nope"\n')
        code, out = run("list")
        self.assertIn("bad", out)
        self.assertIn("BROKEN", out)
        self.assertEqual(run("run", "bad")[0], 1)

    def test_every_shipped_example_is_valid(self):
        examples = sorted(cli.EXAMPLES_DIR.glob("*.toml"))
        self.assertGreaterEqual(len(examples), 3)
        for example in examples:
            with self.subTest(example.stem):
                code, out = run("new", example.stem, "--from", example.stem)
                self.assertEqual(code, 0, out)
        known, broken = act.load_all()
        self.assertEqual(broken, {})
        self.assertFalse(known["morning-repo"].enabled, "a shipped routine starts off")


if __name__ == "__main__":
    unittest.main()
