"""Point HOME, XDG_* and the session bus at a throwaway dir before the package loads.

omarchy_voice.config turns HOME and XDG_* into paths once, at import time, and
other modules copy them by value. So the only safe moment to redirect them is
before the first ``omarchy_voice`` import. Every test file that touches the
package imports this module first, in place of the old ``sys.path`` line
(#99: the suite used to write the user's real session.log, runtime and cache
files, and sent real desktop notifications).

It is a module and not a conftest.py or tests/__init__.py because it must work
under both ``pytest`` and ``python3 -m unittest discover -s tests``; each of
those files is seen by only one runner. Both runners put tests/ on sys.path.

Import it before any ``omarchy_voice`` import. If the package is already loaded
the run stops here, because its paths are already the real ones.
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

# (a) Stop before anything is created: the paths are already real.
_loaded = sorted(m for m in sys.modules if m == "omarchy_voice" or m.startswith("omarchy_voice."))
if _loaded:
    _reason = (
        "tests/_isolated.py was imported after " + ", ".join(_loaded)
        + ": the package already holds the real HOME/XDG paths. "
        "Put `import _isolated` before any omarchy_voice import (#99)."
    )
    if "pytest" in sys.modules:
        raise RuntimeError(_reason)
    # unittest's loader swallows an exception into one failed "test" and runs
    # the rest with real paths, so stop the whole process.
    sys.stderr.write(_reason + "\n")
    sys.stderr.flush()
    os._exit(2)

# (b) The roots the run must never write under.
REAL = [Path.home()] + [
    Path(os.environ[v])
    for v in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR")
    if os.environ.get(v)
]

# (c) The throwaway root, removed when the process exits.
ROOT = Path(tempfile.mkdtemp(prefix="omarchy-voice-tests-"))
atexit.register(shutil.rmtree, ROOT, ignore_errors=True)

# (d) HOME and XDG_* under ROOT. Children inherit os.environ.
os.environ["HOME"] = str(ROOT)
for _v in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
    os.environ.pop(_v, None)
# Claude Code's config dir, which holds MCP secrets (#83). Unset, it falls
# back to ~/.claude.json, which is now under ROOT.
os.environ.pop("CLAUDE_CONFIG_DIR", None)
# The host's installed apps (#129): the gate looks up every entry that runs a
# program, so one could change a policy result. Unset, app_dirs() would fall
# back to /usr/share, so it points at an empty dir under ROOT instead.
os.environ["XDG_DATA_DIRS"] = str(ROOT / "share")
(ROOT / "run").mkdir(mode=0o700)
os.environ["XDG_RUNTIME_DIR"] = str(ROOT / "run")

# (e) A bus address that never exists. Unsetting it would let libnotify
# autolaunch a bus; the real address is absolute, so moving XDG_RUNTIME_DIR
# alone would not cut notify-send off from the desktop.
os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={ROOT / 'run' / 'bus'}"

# (f) What every test file used to do itself.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
