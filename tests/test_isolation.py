"""#99: the suite must never touch the user's real state, runtime or cache files."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

import _isolated

import omarchy_voice.capabilities  # noqa: F401  -- so single-file runs check them too
import omarchy_voice.config  # noqa: F401
import omarchy_voice.feedback  # noqa: F401

TESTS = Path(__file__).resolve().parent


def _under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


class IsolationTests(unittest.TestCase):
    def test_every_path_resolves_under_the_temp_root(self):
        leaks = []
        for name, module in sorted(sys.modules.items()):
            if not (name == "omarchy_voice" or name.startswith("omarchy_voice.")) or module is None:
                continue
            for attr, value in sorted(vars(module).items()):
                if not isinstance(value, Path) or not value.is_absolute():
                    continue
                if any(_under(value, r) for r in _isolated.REAL) and not _under(value, _isolated.ROOT):
                    leaks.append(f"{name}.{attr} = {value}")
        self.assertEqual(leaks, [], "paths outside the throwaway root:\n" + "\n".join(leaks))

    def test_every_test_file_imports_it_before_the_package(self):
        bad = []
        for path in sorted(TESTS.glob("test_*.py")):
            for line in path.read_text().splitlines():
                if line.startswith("import _isolated"):
                    break
                if "omarchy_voice" in line:
                    bad.append(path.name)
                    break
        self.assertEqual(bad, [], "import _isolated before any omarchy_voice import in: " + ", ".join(bad))

    def test_a_parent_claude_config_dir_does_not_reach_a_test(self):
        # Claude Code's config holds MCP secrets (#83). A fresh interpreter, so
        # _isolated runs again with the variable set in its parent environment.
        env = {**os.environ, "CLAUDE_CONFIG_DIR": "/the/real/claude/config"}
        got = subprocess.run(
            [sys.executable, "-c",
             "import os, _isolated; print(os.environ.get('CLAUDE_CONFIG_DIR', 'unset'))"],
            cwd=TESTS, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(got.stdout.strip(), "unset")
        self.assertNotIn("CLAUDE_CONFIG_DIR", os.environ)


if __name__ == "__main__":
    unittest.main()
