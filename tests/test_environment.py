"""Say plainly when the suite is running outside the dev shell.

Run from a bare shell, 31 of these tests fail with messages that look like
real regressions and are not: "wtype is not installed" when wtype is very much
installed, `'return' != 'Return'`, "Hyprland's Lua stub is not installed".
Every one of them passes under `nix develop`. That trap cost three separate
debugging detours before anyone wrote this file.

Two environment facts cause it, both set by flake.nix and neither by a bare
shell:

* `find_library("xkbcommon")` returns None on NixOS -- there is no ldconfig
  cache -- and the bare soname is not on the loader path either, so
  `keys._xkb()` yields None and `canonical_keysym` passes every name through
  unverified. Deliberate (refusing every key on a machine we cannot ask would
  be worse), but it makes the key tests assert on the fallback rather than on
  the behaviour.
* `OMARCHY_VOICE_HL_STUB` points dispatcher validation at Hyprland's LuaLS
  stub. Without it, every dispatcher is refused and those tests assert on
  "not installed" instead of on the routing.

This lives in a discovered test module rather than a conftest or a package
__init__ because those are the two hooks that do NOT fire here: `unittest
discover -s tests` imports each test_*.py directly, so it never loads
`tests/__init__.py` (only `-t .` does), and conftest is pytest-only.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _missing() -> list[str]:
    """What the dev shell provides and this one does not."""
    missing = []
    from omarchy_voice.capabilities import _stub_path
    from omarchy_voice.keys import _xkb

    if _xkb() is None:
        missing.append("libxkbcommon (LD_LIBRARY_PATH) — the key-name tests")
    if _stub_path() is None:
        missing.append("Hyprland's Lua stub (OMARCHY_VOICE_HL_STUB) — the dispatch tests")
    return missing


ADVICE = ("Run `nix develop -c python3 -m unittest discover -s tests`, or "
          "`nix flake check`, for a result worth believing.")

# At import, so the warning is at the TOP of the output where it will be read
# rather than buried among the failures it explains.
if _absent := _missing():
    print("\n".join(["", "=" * 72,
                     "  Running outside the dev shell. Expect failures that are NOT real:",
                     *(f"    - missing {item}" for item in _absent),
                     "", f"  {ADVICE}", "=" * 72, ""]), file=sys.stderr)


class DevShellEnvironmentTests(unittest.TestCase):
    def test_the_environment_the_rest_of_the_suite_assumes_is_present(self):
        """One named failure that explains the other thirty-one.

        Deliberately a failure rather than a skip: a skip is quiet, and the
        point is to be read by someone already staring at a wall of red.
        """
        absent = _missing()
        self.assertEqual(absent, [], f"not the code — the shell. Missing: "
                                     f"{'; '.join(absent)}. {ADVICE}")
