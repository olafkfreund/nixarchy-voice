"""The post-boot hook that moves an enabled plugin to its new id (#18).

The shipped script is run for real against stubbed `omarchy` commands, because
what this has to get right is shell logic -- an "off" plugin that must stay
off, and a marker that must not be written when a step failed.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "nix" / "voice-migrate-ids.sh"

# An absolute path, not /usr/bin/env: the nix check sandbox has no /usr/bin.
BASH = shutil.which("bash")


class MigrationHook(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.calls = self.home / "calls"
        self.marker = self.home / "state" / "omarchy-voice" / "ids-migrated"
        for name in ("omarchy", "systemd-cat"):
            self.stub(name, 'printf \'%s\\n\' "$*" >> "$CALLS"\nexit "${FAIL:-0}"')

    def stub(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(f"#!{BASH}\n{body}\n")
        path.chmod(0o755)

    def plugin_list(self, *enabled: str) -> None:
        rows = [{"id": "voice.indicator", "enabled": "voice.indicator" in enabled},
                {"id": "voice.orb", "enabled": "voice.orb" in enabled}]
        self.stub("omarchy-plugin-list", f"printf '%s' {json.dumps(json.dumps(rows))}")

    def run_hook(self, **env) -> subprocess.CompletedProcess:
        return subprocess.run(
            [BASH, str(HOOK)], capture_output=True, text=True,
            env={**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}",
                 "HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state"),
                 "CALLS": str(self.calls), **env})

    def called(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_an_enabled_plugin_moves_to_its_new_id(self):
        self.plugin_list("voice.indicator")
        self.run_hook()
        self.assertIn("plugin disable voice.indicator", self.called())
        self.assertIn("plugin enable olafkfreund.voice-indicator right", self.called())
        self.assertTrue(self.marker.exists())

    def test_a_plugin_that_was_off_stays_off(self):
        self.plugin_list()
        self.run_hook()
        self.assertEqual([c for c in self.called() if c.startswith("plugin")], [])
        self.assertTrue(self.marker.exists())

    def test_it_runs_once(self):
        self.plugin_list("voice.indicator")
        self.run_hook()
        before = self.called()
        self.run_hook()
        self.assertEqual(self.called(), before)

    def test_a_failure_is_not_recorded_as_done(self):
        self.plugin_list("voice.orb")
        self.run_hook(FAIL="1")
        self.assertFalse(self.marker.exists(), "a failed migration must retry next login")
