---
status: draft
issue: 143
intent: intent/2026-09-25-143-stale-wtype-check.md
---

# Spec: doctor stops reporting wtype, and missing_tools() is deleted

## Decisions on the intent's open questions

The intent was approved without answers to its three questions, so each is
decided here with the evidence. Any of them can be rejected at this gate.

Evidence was taken on this branch (base main `806804a`) with
`git grep -n -i -e wtype -e missing_tools` over the whole tree, excluding
`intent/`, `spec/` and `plan/`. Outside `src/` it finds:

- `tests/`: only negative assertions (`test_policy.py:215`,
  `test_web.py:92` assert wtype is **absent** from the commands) and
  docstrings recounting #60 (`test_policy.py:195-196`, `test_terminal.py:5`,
  `test_environment.py:4`). No test calls `missing_tools` or runs wtype.
- `tools/` (the `verify_*` and bench scripts), `omarchy/`, `plugin/`,
  `share/`, `nix/hm-module.nix`: nothing.
- `nix/package.nix:7,66,69`, `flake.nix:62,128,141`: packaging and its
  comments.
- `README.md:61,219,426`, and one the intent missed:
  `docs/omarchy-voice.html:523`, the same diagram as the README
  ("hyprctl · omarchy · wtype · uwsm-app").
- `HANDOFF.md:156,519`: history.

### 1. `missing_tools()`: delete it

Deleted. It has no caller in `src/`, `tests/`, `tools/`, `nix/` or anywhere
else, and its list is wrong twice: it names `wtype`, which nothing runs, and
omits `ai-mirror-input`, which clicking needs. Giving it a caller would
create a second tool list to keep in step with doctor's, which is how this
one went stale. `shutil` stays imported in `capabilities.py` only if
something else there uses it. The implementer checks and drops the import
if nothing does.

### 2. What doctor's hands line checks: drop wtype, add the tools that are actually run

The hands loop (`cli.py:504`) becomes a list of `(tool, what it is for)`
pairs. Each line keeps its tick and gains a short purpose, so a cross tells
the user what stops working:

| Tool | Purpose shown |
| --- | --- |
| `hyprctl` | dispatch and typing |
| `omarchy` | omarchy commands |
| `notify-send` | notifications |
| `uwsm-app` | launching apps (falls back to gtk-launch) |
| `ai-mirror-input` | clicking and scrolling |
| `grim` | screenshots for reading the screen |
| `tesseract` | reading text off the screen |

`ai-mirror-input` goes in because it is used (`virtual_input.HELPER`,
`virtual_input.py:58`) and nothing in doctor checks it. Doctor uses
`shutil.which(virtual_input.HELPER)`, not a second copy of the string. It
does not spawn the helper, per the intent's constraint.

`grim` and `tesseract` go in too. They are genuinely needed: the
screen-reading tools shell out to them (`tools.py:2997`, `3048`, `3168`),
and they are in the wrapper's `runtimeInputs`. Those tools do report
"not installed" when called, but only in the middle of a voice turn. Doctor
is where a user outside the Nix wrapper, for example on a pip install,
decides what to install. A `which` costs nothing, and the purpose column
says why each one is there.

Not added: `pw-record` (the ears section already reports it through
`listen_local`), `wl-clipboard` and `tmux` (out of scope for #143; the
intent names only the missing pointer helper and the screen pair).

### 3. wtype leaves the package, the dev shell, the check and the docs, in this change

Removed everywhere in the same change. Doctor, the wrapper and the README
all describe what the daemon needs, and one PR that makes them agree is
smaller to review than two that leave them disagreeing in between. Nothing
runs wtype, so removing it from PATH cannot change behaviour. The check
sandbox proves the suite does not depend on it.

- `nix/package.nix`: drop the `wtype` argument (line 7) and the
  `runtimeInputs` entry (line 69). Rewrite the comment at 65-67 to use a
  tool that is used as its example: "a missing ai-mirror-input means the
  model silently cannot click".
- `flake.nix:62` (devShell) and `flake.nix:128` (check PATH): drop
  `pkgs.wtype`. `flake.nix:141`: "grim and wtype" becomes "grim and
  tesseract".
- `README.md:61`: diagram becomes `hyprctl · omarchy · uwsm-app ·
  ai-mirror-input`. `README.md:219`: `(`grim`, `tesseract`,
  `whisper-cpp`, ...)`. `README.md:426`: drop `wtype` from the list.
- `docs/omarchy-voice.html:523`: same change as the README diagram.

Left alone, on purpose: `HANDOFF.md`, the test docstrings, and the comments
at `tools.py:369`, `tools.py:3978-3989` and `keys.py:217`. They record
*why* wtype was removed (#60) and stay true.

## Design

1. `src/omarchy_voice/cli.py`: move the hands tool check into a small
   module-level function, `_hands_tools() -> list[str]`, that returns the
   printed lines for the seven tools above. `cmd_doctor` prints what it
   returns in place of the current loop. It is a function only so a test
   can call it without running all of `cmd_doctor`, which reads the mic,
   the compositor and the manifest.
2. `src/omarchy_voice/capabilities.py`: delete `missing_tools()`
   (lines 1388-1389).
3. Packaging and docs as in decision 3.

No behaviour of any tool changes. Doctor's output changes only in the
hands section.

## Alternatives rejected

- **Keep `missing_tools()` and have doctor call it.** Doctor's list needs a
  purpose for each tool and a different set of tools. Making one function
  serve both means changing its return type for a single caller. Deleting
  it is smaller.
- **Just delete the wtype line from doctor.** That leaves the one real
  unchecked hands dependency, `ai-mirror-input`, unchecked. The intent's
  outcome is "every tool the hands section lists is one the daemon
  actually runs"; adding the missing ones costs three lines.
- **Probe `ai-mirror-input` by starting it.** Ruled out by the intent. It
  opens Wayland globals, and doctor stays read-only.
- **Leave wtype in the package and change only doctor.** It keeps a dead
  dependency in the closure. The package comment would still cite it as
  the reason the wrapper exists, and the README would still list it.
- **Separate PR for packaging and README.** Two reviews for one fact, and
  the docs contradict doctor in between.

## Risks

- **A test depends on wtype being on PATH after all.** The grep says none
  does, and the flake check runs the whole suite without it, so this would
  show up at verification rather than on a user's machine.
- **A user script outside this repo calls `wtype` through the wrapper's
  PATH.** The wrapper's PATH is for the daemon. Anyone who wants wtype
  installs it themselves. Low.
- **Screen-reading lines show a cross on a pip install without grim or
  tesseract.** Intended: those tools are then broken, and now the user can
  see why.

## Verification

Tests first, failing on this branch before the change:

- `tests/test_doctor_hands.py` (new, `unittest`, like the rest):
  - with `shutil.which` patched to return `None`, `cli._hands_tools()`
    contains no line mentioning `wtype`, and has one crossed line each for
    `hyprctl`, `omarchy`, `notify-send`, `uwsm-app`, `ai-mirror-input`,
    `grim` and `tesseract`;
  - `capabilities` has no attribute `missing_tools`.

  Both fail now: `_hands_tools` does not exist, and `missing_tools` does.

Then, under `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`:

- `nix develop -c pytest tests -q`: all green.
- Grep: `git grep -n -i -e wtype -e missing_tools -- src nix flake.nix
  README.md docs` prints only the four history comments in `tools.py`
  (369, 3978, 3989) and `keys.py` (217).
- `nix build`: succeeds, and `grep -c wtype result/bin/omarchy-voice`
  prints 0.
- `nix flake check`: passes, which runs the suite in the sandbox without
  wtype on PATH.
