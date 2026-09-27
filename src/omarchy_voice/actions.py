"""Actions and routines: named, reusable recipes of steps (#157).

An action is a TOML file in ~/.config/omarchy-voice/actions/<name>.toml. Each
step is one of Oma's own tools with fixed arguments, a plain-words `ask` for the
model, or another action by name. A `[schedule]` table makes it a routine.

Nothing here is a second way to act. Every tool step goes through the
Executor's gate like a spoken one; what a saved action adds is only that a step
the user approved once does not ask again (see approvals below).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import config as cfg

ACTIONS_DIR = cfg.CONFIG_HOME / "omarchy-voice" / "actions"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_DEPTH = 3
EVERY_RE = re.compile(r"^every\s+(\d+)\s*([smhd])$")
_TOP_KEYS = {"description", "phrases", "step", "schedule"}
_STEP_KEYS = {"tool", "args", "ask", "action"}
_SCHEDULE_KEYS = {"when", "enabled"}


class ActionError(ValueError):
    """A recipe that cannot be loaded, saved or run, with the reason."""


@dataclass
class Step:
    tool: str = ""
    args: dict = field(default_factory=dict)
    ask: str = ""
    action: str = ""

    @property
    def kind(self) -> str:
        return "tool" if self.tool else "ask" if self.ask else "action"


@dataclass
class Action:
    name: str
    description: str = ""
    phrases: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    when: str = ""
    enabled: bool = False


def path_for(name: str) -> Path:
    return ACTIONS_DIR / f"{name}.toml"


def check_name(name: str) -> None:
    if not NAME_RE.match(name or ""):
        raise ActionError(f"bad action name {name!r}: use lowercase letters, digits and dashes")


def check_when(when: str) -> None:
    """`login`, `every <N><s|m|h|d>`, or a systemd OnCalendar expression.

    ponytail: an OnCalendar string is only checked for being non-empty here;
    write_timers asks systemd-analyze, which is the real parser.
    """
    if not when.strip():
        raise ActionError("schedule.when is empty")


def parse(name: str, data: dict) -> Action:
    """Structure only: every key known, every step exactly one kind."""
    check_name(name)
    if unknown := set(data) - _TOP_KEYS:
        raise ActionError(f"unknown key(s): {', '.join(sorted(unknown))}")
    phrases = data.get("phrases", [])
    if not isinstance(phrases, list) or not all(isinstance(p, str) for p in phrases):
        raise ActionError("phrases must be a list of strings")
    raw_steps = data.get("step", [])
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ActionError("an action needs at least one [[step]]")
    steps = []
    for n, raw in enumerate(raw_steps, 1):
        if not isinstance(raw, dict):
            raise ActionError(f"step {n}: must be a table")
        if unknown := set(raw) - _STEP_KEYS:
            raise ActionError(f"step {n}: unknown key(s): {', '.join(sorted(unknown))}")
        kinds = [k for k in ("tool", "ask", "action") if raw.get(k)]
        if len(kinds) != 1:
            raise ActionError(f"step {n}: needs exactly one of tool, ask or action")
        if "args" in raw and not raw.get("tool"):
            raise ActionError(f"step {n}: args belong to a tool step")
        if not isinstance(raw.get(kinds[0]), str):
            raise ActionError(f"step {n}: {kinds[0]} must be a string")
        if not isinstance(raw.get("args", {}), dict):
            raise ActionError(f"step {n}: args must be a table")
        steps.append(Step(tool=raw.get("tool", ""), args=raw.get("args", {}),
                          ask=raw.get("ask", ""), action=raw.get("action", "")))
    schedule = data.get("schedule", {})
    if not isinstance(schedule, dict):
        raise ActionError("schedule must be a table")
    if unknown := set(schedule) - _SCHEDULE_KEYS:
        raise ActionError(f"schedule: unknown key(s): {', '.join(sorted(unknown))}")
    when = schedule.get("when", "")
    if schedule:
        if not isinstance(when, str):
            raise ActionError("schedule.when must be a string")
        check_when(when)
    return Action(name=name, description=str(data.get("description", "")),
                  phrases=phrases, steps=steps, when=when,
                  enabled=bool(schedule.get("enabled", False)))


def _check_tool_step(n: int, step: Step, allow_shell: bool) -> None:
    """The tool exists and the arguments fit its schema.

    ponytail: schema shape only (required keys, no unknown keys). The tool's
    own _validate_* runs when the step runs, inside the gate; running it here
    would need an Executor and some validators look at the live desktop.
    """
    from .tools import TOOL_SCHEMAS  # tools imports this module
    schema = next((s for s in TOOL_SCHEMAS if s["name"] == step.tool), None)
    if schema is None:
        raise ActionError(f"step {n}: unknown tool {step.tool!r}")
    if step.tool == "run_shell" and not allow_shell:
        raise ActionError(f"step {n}: run_shell needs allow_shell = true; "
                          "use run_in_terminal instead")
    spec = schema["input_schema"]
    missing = [k for k in spec.get("required", []) if k not in step.args]
    if missing:
        raise ActionError(f"step {n}: {step.tool} needs {', '.join(missing)}")
    if spec.get("additionalProperties") is False:
        extra = set(step.args) - set(spec.get("properties", {}))
        if extra:
            raise ActionError(f"step {n}: {step.tool} does not take {', '.join(sorted(extra))}")


def check(action: Action, known: dict[str, Action], allow_shell: bool) -> None:
    """Cross-references: tools exist, nested actions exist, no cycle, depth <= 3."""
    for n, step in enumerate(action.steps, 1):
        if step.kind == "tool":
            _check_tool_step(n, step, allow_shell)
        elif step.kind == "action" and step.action not in known:
            raise ActionError(f"step {n}: no action called {step.action!r}")

    def walk(name: str, trail: tuple[str, ...]) -> None:
        if name in trail:
            raise ActionError("cycle: " + " -> ".join((*trail, name)))
        if len(trail) >= MAX_DEPTH:
            raise ActionError(f"nested deeper than {MAX_DEPTH}: " + " -> ".join((*trail, name)))
        for step in known[name].steps:
            if step.kind == "action" and step.action in known:
                walk(step.action, (*trail, name))

    walk(action.name, ())


def load_all(allow_shell: bool = False) -> tuple[dict[str, Action], dict[str, str]]:
    """Every action on disk, and every file that is broken with why."""
    parsed: dict[str, Action] = {}
    broken: dict[str, str] = {}
    for path in sorted(ACTIONS_DIR.glob("*.toml")):
        try:
            parsed[path.stem] = parse(path.stem, tomllib.loads(path.read_text()))
        except (OSError, tomllib.TOMLDecodeError, ActionError) as exc:
            broken[path.stem] = str(exc)
    good: dict[str, Action] = {}
    for name, action in parsed.items():
        try:
            check(action, parsed, allow_shell)
            good[name] = action
        except ActionError as exc:
            broken[name] = str(exc)
    return good, broken


def load(name: str, allow_shell: bool = False) -> Action:
    check_name(name)
    actions, broken = load_all(allow_shell)
    if name in broken:
        raise ActionError(f"{name}: {broken[name]}")
    if name not in actions:
        raise ActionError(f"no action called {name!r}")
    return actions[name]


# -- writing ------------------------------------------------------------------
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(k: str) -> str:
    return k if _BARE_KEY.match(k) else json.dumps(k, ensure_ascii=False)


def _value(v) -> str:
    # TOML basic strings take JSON's escapes, so json.dumps is a correct quoter.
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(x)}"
                                for k, x in v.items() if x is not None) + " }"
    raise ActionError(f"cannot write {type(v).__name__} to TOML")


def render_toml(action: Action) -> str:
    lines = [f"# Oma action. Run: omarchy-voice action run {action.name}"]
    if action.description:
        lines.append(f"description = {_value(action.description)}")
    if action.phrases:
        lines.append(f"phrases = {_value(action.phrases)}")
    for step in action.steps:
        lines += ["", "[[step]]"]
        if step.kind == "tool":
            lines.append(f"tool = {_value(step.tool)}")
            if step.args:
                lines.append(f"args = {_value(step.args)}")
        else:
            lines.append(f"{step.kind} = {_value(getattr(step, step.kind))}")
    if action.when:
        lines += ["", "[schedule]", f"when = {_value(action.when)}",
                  f"enabled = {_value(action.enabled)}"]
    return "\n".join(lines) + "\n"


def _has_comments(text: str) -> bool:
    # ponytail: whole-line comments only; a trailing `x = 1  # note` is missed.
    return any(line.lstrip().startswith("#") for line in text.splitlines()[1:])


def _writable(path: Path) -> None:
    """Only a regular file the user owns is Oma's to rewrite or remove.

    A symlink is how Home Manager installs a declared action; it points into
    the read-only store and belongs to the Nix config, not to Oma.
    """
    if path.is_symlink():
        raise ActionError(f"{path.stem} is declared in Home Manager; change it there")
    if path.exists() and path.stat().st_uid != os.getuid():
        raise ActionError(f"{path} is not owned by you")


def save(action: Action, *, force: bool = False) -> Path:
    check_name(action.name)
    path = path_for(action.name)
    _writable(path)
    if path.exists() and not force and _has_comments(path.read_text()):
        raise ActionError(f"{path} has comments that saving would drop; "
                          "edit it by hand (omarchy-voice action edit "
                          f"{action.name}) or save with force")
    text = render_toml(action)
    parse(action.name, tomllib.loads(text))  # never write what cannot be read back
    ACTIONS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return path


def delete(name: str) -> Path:
    """Move to actions/.trash/ rather than unlink: a misheard delete is undoable."""
    check_name(name)
    path = path_for(name)
    if not path.exists() and not path.is_symlink():
        raise ActionError(f"no action called {name!r}")
    _writable(path)
    trash = ACTIONS_DIR / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.toml"
    path.replace(dest)
    return dest


def set_enabled(name: str, on: bool) -> Action:
    """Turn a routine on or off by editing one line, so the user's comments stay.

    Shared by `omarchy-voice action enable|disable` and the `action` tool (#167).
    """
    check_name(name)
    path = path_for(name)
    if not path.exists() and not path.is_symlink():
        raise ActionError(f"no action called {name!r}")
    _writable(path)
    text = path.read_text()
    value = "true" if on else "false"
    # ponytail: line-based; an inline `schedule = { when = … }` is refused by the
    # parse below rather than edited. A TOML writer that keeps comments if needed.
    new, count = re.subn(r"(?m)^(\s*enabled\s*=\s*)(true|false)\b", rf"\g<1>{value}", text)
    if not count:
        new, count = re.subn(r"(?m)^(\s*when\s*=.*)$", rf"\g<1>\nenabled = {value}", text, count=1)
    if not count:
        raise ActionError("no [schedule] with a `when` to turn on; add one first")
    try:
        action = parse(name, tomllib.loads(new))
    except tomllib.TOMLDecodeError as exc:
        raise ActionError(f"{path} could not be edited safely: {exc}") from exc
    if not action.when or action.enabled != on:
        raise ActionError(f"{path} could not be edited safely; edit it by hand")
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(new)
    tmp.replace(path)
    return action


# -- approvals ----------------------------------------------------------------
def approval_key(description: str) -> str:
    """What an approval is keyed on: the step exactly as the gate described it.

    Editing the step changes its description, so the approval lapses by itself.
    """
    return hashlib.sha256(description.encode()).hexdigest()


APPROVALS_FILE = cfg.STATE_HOME / "omarchy-voice" / "approvals.json"


def approvals() -> dict[str, list[str]]:
    try:
        data = json.loads(APPROVALS_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def grant(owner: str, descriptions: list[str]) -> None:
    """Record that the user approved these steps of `owner`. Only a human says so:
    `action approve`, or a spoken confirm of a save or of a held step."""
    data = approvals()
    keys = set(data.get(owner, []))
    keys.update(approval_key(d) for d in descriptions)
    data[owner] = sorted(keys)
    APPROVALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = APPROVALS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.chmod(0o600)
    tmp.replace(APPROVALS_FILE)


def revoke(owner: str) -> None:
    data = approvals()
    if data.pop(owner, None) is not None:
        APPROVALS_FILE.write_text(json.dumps(data, indent=1))


# -- running ------------------------------------------------------------------
@dataclass
class RunResult:
    ok: bool
    lines: list[str] = field(default_factory=list)
    # Why it stopped short, in words the user can be told.
    stopped: str = ""
    # (owner action, step number, gate description) of a step left waiting.
    held: tuple[str, int, str] | None = None
    # In a model turn: what the model is to do next, then how to carry on.
    yielded: str = ""

    def summary(self) -> str:
        return "\n".join([*self.lines, *filter(None, [self.stopped, self.yielded])])


def flatten(action: Action, known: dict[str, Action]) -> list[tuple[str, Step]]:
    """Nested actions expanded in place, each step tagged with the action it
    belongs to (approvals are per action). check() has ruled out cycles."""
    out: list[tuple[str, Step]] = []
    for step in action.steps:
        if step.kind == "action":
            out.extend(flatten(known[step.action], known))
        else:
            out.append((action.name, step))
    return out


def run(action: Action, known: dict[str, Action], executor, *, ask, start: int = 1) -> RunResult:
    """Run the steps in order from `start`, stopping at the first failure or hold.

    `ask(text, n)` does an ask step. It returns (ok, reply), or None to hand
    the step back to a model that is already in a turn: the run then stops
    with `yielded` telling the model what to do and where to pick up.

    Tool steps go to `_call_locked`: the caller holds the Executor lock (the
    `action` tool runs inside a tool call) or no other thread exists (the CLI).
    """
    flat = flatten(action, known)
    total = len(flat)
    grants = approvals()
    result = RunResult(ok=True)
    for n, (owner, step) in enumerate(flat, 1):
        if n < start:
            continue
        if step.kind == "tool":
            before = executor.pending
            r = executor._call_locked(step.tool, dict(step.args),
                                      approved=frozenset(grants.get(owner, ())))
            if executor.pending is not None and executor.pending is not before:
                held = executor.describe(*executor.pending)
                result.ok = False
                result.held = (owner, n, held)
                result.stopped = f"stopped at step {n} of {total}: {held} needs your approval"
                return result
            if not r.ok:
                result.ok = False
                result.stopped = f"stopped at step {n} of {total}: {r.output}"
                return result
            result.lines.append(f"{n}. {r.output or 'done'}")
        else:
            out = ask(step.ask, n)
            if out is None:
                rest = (f" Then call the action tool with do=run, name={action.name}, "
                        f"start={n + 1}." if n < total else "")
                result.yielded = f"Step {n} of {total} is yours to do now: {step.ask}{rest}"
                return result
            ok, reply = out
            if not ok:
                result.ok = False
                result.stopped = f"stopped at step {n} of {total}: {reply}"
                return result
            result.lines.append(f"{n}. {reply}")
    return result


def held_steps(action: Action, known: dict[str, Action], config) -> list[tuple[str, int, str]]:
    """Every tool step the gate would hold, found by asking the real gate.

    A dry-run Executor runs nothing, but the gate still decides; the same
    launch resolution and shell-off rules apply as on a real run. Steps that
    are denied are not listed: approval cannot change a deny.
    """
    import dataclasses
    from .tools import Executor
    probe = Executor(dataclasses.replace(config, dry_run=True))
    found = []
    for n, (owner, step) in enumerate(flatten(action, known), 1):
        if step.kind != "tool":
            continue
        probe._call_locked(step.tool, dict(step.args))
        if probe.pending is not None:
            found.append((owner, n, probe.describe(*probe.pending)))
            probe.pending = None
    return found


# -- the Omarchy menu ---------------------------------------------------------
# Omarchy 4.0.4 runs only its own built-in menu providers (Menu.qml:338), so
# the rows are written into the user's menu file, which the shell watches and
# reloads live (Menu.qml:931). Only the block between the markers is Oma's.
MENU_FILE = cfg.CONFIG_HOME / "omarchy" / "extensions" / "omarchy-menu.jsonc"
MENU_BEGIN = "  // >>> omarchy-voice actions (generated: edit the actions, not these lines)"
MENU_END = "  // <<< omarchy-voice actions"
TERMINAL = "omarchy-launch-floating-terminal-with-presentation"


def unit_name(action: Action) -> str:
    """The systemd unit whose state is the routine's on/off."""
    kind = "service" if action.when.strip() == "login" else "timer"
    return f"omarchy-voice-routine-{action.name}.{kind}"


def menu_rows(known: dict[str, Action]) -> dict[str, dict]:
    rows: dict[str, dict] = {
        "voice": {"icon": "\U000f036c", "label": "Voice", "description": "Oma: actions and routines"},
        "voice.actions": {"icon": "\U000f0e1e", "label": "Actions"},
    }
    for name, a in sorted(known.items()):
        base = f"voice.actions.{name}"
        rows[base] = {"icon": "\U000f040a" if not a.when else "\U000f0954", "label": name,
                      "description": a.description or ", ".join(a.phrases)}
        rows[f"{base}.run"] = {"icon": "\U000f040a", "label": "Run",
                               "action": f"omarchy-voice action run {name}"}
        rows[f"{base}.edit"] = {"icon": "\U000f03eb", "label": "Edit",
                                "action": f"{TERMINAL} omarchy-voice action edit {name}"}
        rows[f"{base}.approve"] = {
            "icon": "\U000f0133", "label": "Approve steps…",
            "description": "Steps that ask for confirmation, approved once for good",
            "action": f"{TERMINAL} omarchy-voice action approve {name}"}
        if a.when:
            unit = unit_name(a)
            rows[f"{base}.routine"] = {
                "icon": "\U000f0954", "label": f"Routine: {a.when}",
                "checked": f"systemctl --user is-enabled -q {unit}",
                "action": (f"if systemctl --user is-enabled -q {unit}; then "
                           f"omarchy-voice action disable {name}; else "
                           f"omarchy-voice action enable {name}; fi")}
        rows[f"{base}.delete"] = {"icon": "\U000f01b4", "label": "Delete",
                                  "description": "Moves it to the trash folder",
                                  "action": f"omarchy-voice action delete {name}"}
    rows["voice.new"] = {"icon": "\U000f0415", "label": "New action…",
                         "action": f"{TERMINAL} omarchy-voice action new"}
    rows["voice.ask"] = {"icon": "\U000f036c", "label": "Ask Oma to make one",
                         "description": "Starts listening; say what the action should do",
                         "action": "omarchy-voice listen start"}
    rows["voice.folder"] = {"icon": "\U000f024b", "label": "Open actions folder",
                            "action": f"xdg-open {ACTIONS_DIR}"}
    return rows


def _menu_block(known: dict[str, Action]) -> str:
    lines = [MENU_BEGIN]
    lines += [f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},"
              for k, v in menu_rows(known).items()]
    lines.append(MENU_END)
    return "\n".join(lines)


def _with_comma(head: str) -> str:
    """Give the last entry before our block its trailing comma, on its own line
    even when comments follow it. ponytail: whole-line // comments only; an
    entry ending in `} // note` would get the comma after the note."""
    lines = head.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        if line and not line.startswith("//"):
            if not line.endswith((",", "{")):
                lines[i] = lines[i].rstrip() + ","
            break
    return "\n".join(lines)


def write_menu_rows(known: dict[str, Action]) -> str | None:
    """Rewrite our block in the user's menu file. Returns why it did not, or None."""
    path = MENU_FILE
    if path.is_symlink():
        return f"{path} is managed by Home Manager; the Voice menu was not written"
    block = _menu_block(known)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "{\n" + block + "\n}\n"
    else:
        old = path.read_text()
        begin, end = old.find(MENU_BEGIN), old.find(MENU_END)
        if begin >= 0 and end > begin:
            text = old[:begin] + block + old[end + len(MENU_END):]
        elif begin >= 0 or end >= 0:
            return f"{path} has only one of the omarchy-voice markers; fix it by hand"
        else:
            close = old.rstrip().rfind("}")
            if close < 0:
                return f"{path} is not a JSONC object; the Voice menu was not written"
            head = old[:close].rstrip()
            gap = old[len(head):close] or "\n"  # the user's own blank lines stay
            text = f"{_with_comma(head)}\n\n{block}{gap}{old[close:]}"
        if text == old:
            return None
        (path.parent / (path.name + ".bak-omarchy-voice")).write_text(old)
    tmp = path.with_suffix(".jsonc.tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return None


# -- routines: systemd user timers ---------------------------------------------
UNIT_DIR = cfg.CONFIG_HOME / "systemd" / "user"
UNIT_PREFIX = "omarchy-voice-routine-"


def _systemctl(*args: str) -> tuple[bool, str]:
    """The one place a routine touches systemd. Tests replace it."""
    try:
        r = subprocess.run(["systemctl", "--user", *args], capture_output=True,
                           text=True, timeout=20)
        return r.returncode == 0, (r.stderr or r.stdout).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def _calendar_ok(when: str) -> str | None:
    """systemd's own verdict on an OnCalendar string, when it is there to ask."""
    if not shutil.which("systemd-analyze"):
        return None
    try:
        r = subprocess.run(["systemd-analyze", "calendar", when],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None if r.returncode == 0 else (r.stderr.strip() or "not a calendar time")


def default_launcher() -> str:
    """omarchy-voice-keyed when Home Manager installed it: it reads the API
    keys the daemon's own unit reads, which a routine's ask step needs too."""
    return (shutil.which("omarchy-voice-keyed") or shutil.which("omarchy-voice")
            or str(Path(sys.argv[0]).resolve()))


def unit_texts(action: Action, launcher: str) -> dict[str, str]:
    """File name -> contents for a routine's units."""
    when = action.when.strip()
    head = (f"[Unit]\nDescription=Oma routine: {action.name}\n"
            "PartOf=graphical-session.target\nAfter=graphical-session.target\n")
    service = (head + "\n[Service]\nType=oneshot\n"
               f"ExecStart={launcher} action run {action.name} --unattended\n")
    stem = f"{UNIT_PREFIX}{action.name}"
    if when == "login":
        return {f"{stem}.service": service + "\n[Install]\nWantedBy=graphical-session.target\n"}
    if m := EVERY_RE.match(when):
        schedule = f"OnBootSec=2m\nOnUnitActiveSec={m[1]}{m[2]}\n"
    else:
        # Persistent: a morning routine missed while asleep runs on wake.
        schedule = f"OnCalendar={when}\nPersistent=true\n"
    timer = (head + f"\n[Timer]\n{schedule}"
             "\n[Install]\nWantedBy=graphical-session.target\n")
    return {f"{stem}.service": service, f"{stem}.timer": timer}


def write_timers(known: dict[str, Action], launcher: str) -> list[str]:
    """Make the unit files match the enabled routines, and tell systemd.

    Files that are symlinks belong to Home Manager and are never touched.
    Returns what the user should be told.
    """
    notes: list[str] = []
    wanted: dict[str, str] = {}
    start: list[str] = []
    for a in known.values():
        if not (a.when and a.enabled):
            continue
        if not EVERY_RE.match(a.when.strip()) and a.when.strip() != "login":
            if why := _calendar_ok(a.when):
                notes.append(f"routine {a.name} not scheduled: {why}")
                continue
        texts = unit_texts(a, launcher)
        wanted.update(texts)
        start.append(unit_name(a))
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    stale = [p for p in UNIT_DIR.glob(f"{UNIT_PREFIX}*")
             if not p.is_symlink() and p.name not in wanted]
    # Disabled while the file still exists, or its .wants link is left dangling.
    for path in stale:
        _systemctl("disable", "--now", path.name)
        path.unlink()
    changed = bool(stale)
    for name, text in wanted.items():
        path = UNIT_DIR / name
        if path.is_symlink():
            notes.append(f"{name} is managed by Home Manager; left alone")
            start = [u for u in start if u != name]
            continue
        if not path.exists() or path.read_text() != text:
            path.write_text(text)
            changed = True
    if not changed:
        return notes
    ok, err = _systemctl("daemon-reload")
    if not ok:
        return notes + [f"systemctl daemon-reload failed: {err}"]
    for unit in start:
        # A login routine is enabled, not started: turning it on is not login.
        args = ("enable", unit) if unit.endswith(".service") else ("enable", "--now", unit)
        ok, err = _systemctl(*args)
        if not ok:
            notes.append(f"could not enable {unit}: {err}")
    return notes


def after_change(config) -> list[str]:
    """Bring the menu and the timers in line with the files.

    Called after every save, delete, enable and disable, so neither can drift.
    Returns anything the user should be told (a file it could not write).
    """
    known, _ = load_all(config.allow_shell)
    notes = []
    if why := write_menu_rows(known):
        notes.append(why)
    notes += write_timers(known, config.routines_launcher or default_launcher())
    return notes
