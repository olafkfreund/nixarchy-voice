---
status: draft
issue: 143
spec: spec/2026-09-25-143-stale-wtype-check.md
---

# Plan: doctor stops reporting wtype, and missing_tools() is deleted

Closes #143. Base: `main` `806804a` (1170 tests). Every `file:line` below
was checked against `806804a`. Files that change: `src/omarchy_voice/cli.py`,
`src/omarchy_voice/capabilities.py`, `nix/package.nix`, `flake.nix`,
`README.md`, `docs/omarchy-voice.html`, and the new
`tests/test_doctor_hands.py`. Nothing else changes. `HANDOFF.md`, `tests/`
docstrings, and the history comments in `tools.py` and `keys.py` are left
as they are on purpose.

## Approved decisions, carried over from the spec

1. **`missing_tools()` is deleted** (`capabilities.py:1388-1389`). Nothing in
   `src/`, `tests/`, `tools/` or `nix/` calls it, and its list is wrong
   twice: it names `wtype`, which nothing runs, and leaves out
   `ai-mirror-input`, which clicking needs. It gets no new caller, because a
   second tool list would drift the same way.
2. **`import shutil` stays in `capabilities.py`.** It is still used at
   `:1306` (`_cache_key` agent stamp) and `:1369` (`coding_agents`). The spec
   says to drop the import only if nothing uses it. Two things do, so it
   stays.
3. **Doctor's hands check becomes a list of `(tool, purpose)` pairs**, in
   this order, with these purposes:

   | Tool | Purpose shown |
   | --- | --- |
   | `hyprctl` | dispatch and typing |
   | `omarchy` | omarchy commands |
   | `notify-send` | notifications |
   | `uwsm-app` | launching apps (falls back to gtk-launch) |
   | `ai-mirror-input` | clicking and scrolling |
   | `grim` | screenshots for reading the screen |
   | `tesseract` | reading text off the screen |

   `wtype` is removed from it. Each line keeps its tick and gains the purpose.
4. **`ai-mirror-input` is looked up as `shutil.which(virtual_input.HELPER)`**
   (`virtual_input.py:58`), not as a second copy of the string. Doctor only
   runs `which`. It never spawns the helper, because the helper opens Wayland
   globals and doctor stays read-only.
5. **`grim` and `tesseract` are checked** because the screen-reading tools
   shell out to them (`tools.py:2997`, `:3048`, `:3168`) and the wrapper puts
   them on `PATH`. Doctor is where a user without the Nix wrapper, such as a
   pip install, finds out what to install.
6. **Not added:** `pw-record` (the ears section already covers it),
   `wl-clipboard` and `tmux` (out of scope for #143).
7. **The check moves into a module-level function** in `cli.py`,
   `_hands_tools() -> list[str]`, which returns the printed lines for the
   seven tools. `cmd_doctor` (`cli.py:371`) prints what it returns in place
   of the loop at `cli.py:504-505`. It is a function only so a test can call
   it without running the rest of `cmd_doctor`, which reads the mic, the
   compositor and the manifest. The compositor-events lines that follow
   (`:509-515`) are not part of it and do not change.
8. **wtype leaves the package, the dev shell, the check and the docs in this
   change:**
   - `nix/package.nix:7`: drop the `wtype` argument. `:69`: drop the
     `runtimeInputs` entry. `:65-67`: rewrite the comment so its example is
     a tool that is actually used: "a missing ai-mirror-input means the model
     silently cannot click".
   - `flake.nix:62` (devShell) and `flake.nix:128` (check `PATH`): drop
     `pkgs.wtype`. `flake.nix:141`: "grim and wtype" becomes "grim and
     tesseract".
   - `README.md:61`: the diagram becomes
     `hyprctl · omarchy · uwsm-app · ai-mirror-input`. `README.md:219-220`:
     the list becomes `` (`grim`, `tesseract`, `whisper-cpp`, ...) ``.
     `README.md:426`: drop `` `wtype`, `` from the list.
   - `docs/omarchy-voice.html:523`: the same change as the README diagram:
     `hyprctl · omarchy · uwsm-app · ai-mirror-input, then a notification and
     bar state.`
9. **Left alone on purpose:** `HANDOFF.md:156,519`, the test docstrings
   (`test_policy.py:195-196`, `test_terminal.py:5`, `test_environment.py:4`),
   the negative assertions (`test_policy.py:215`, `test_web.py:92`), and the
   comments at `tools.py:369`, `tools.py:3978`, `tools.py:3989` and
   `keys.py:217`. They record why wtype was removed (#60), and that is still
   true. There is no changelog file in the repo.
10. **No tool's behaviour changes.** Doctor's output changes only in the
    hands section.
11. **Rejected, and not to be reintroduced:** keeping `missing_tools()` and
    having doctor call it; only deleting the wtype line (which leaves
    `ai-mirror-input` unchecked); probing `ai-mirror-input` by starting it;
    changing doctor but leaving wtype in the package; a separate PR for
    packaging and the README.

## Spec ambiguities, resolved here (flagged for the reviewer)

- **A. The line format.** The spec says each line "keeps its tick and gains
  a short purpose", but does not give the format. Resolution:
  `f"  {_tick(ok)} {tool} — {purpose}"`. The two-space indent and the tick
  come from the current loop (`cli.py:505`), and the em dash matches the
  `barge_in` line above it (`:494`).
- **B. `cli.py` does not import `virtual_input` on main** (the import is at
  `:15-16`: `__version__, capabilities, config as cfg, hypr_events,
  listen_local`). Decision 4 needs it, so `virtual_input` is added to that
  import. The module imports only the standard library (`virtual_input.py:46-52`)
  and does nothing when imported, so this adds no side effect to
  `omarchy-voice` start-up.
- **C. `_tick` depends on whether stdout is a terminal** (`cli.py:29-30`).
  A missing tool is always `✗`. A present tool is `✓`, wrapped in colour
  codes only on a terminal. So the tests look for `✓` or `✗` inside the
  line, and do not compare whole lines. That way they pass under
  `pytest -q` and under `unittest` in a terminal.
- **D. The spec's grep expectation reads "only the four history comments in
  `tools.py` (369, 3978, 3989) and `keys.py` (217)".** That is four lines
  (three in `tools.py`, one in `keys.py`), not four in `tools.py`. Step 7
  expects exactly those four lines.

## Landing order

**#143 lands LAST** in this batch: #144, then #145, then #143. None of the
three touches another's source files. #144 edits `src/omarchy_voice/config.py`;
#145 edits `src/omarchy_voice/tools.py:792-796` and nearby lines; #143 edits
neither file. The only file all three edit is **`README.md`**:

- **#144** changes the "Rule names" block under `## Safety`:
  `README.md:884` "Deny (29):" becomes "Deny (35)", six table rows are added
  below it, and one paragraph is added next to the symlink note.
- **#145** (spec section 3) adds a sentence to the shell-off text. That is
  either the `## Terminals` paragraph at `README.md:573-574` or the
  `## Safety` shell-off bullet at `README.md:786-795`, which says "in a
  terminal window". The spec does not say which.
- **#143** edits `README.md:61`, `:219-220` and `:426`.

Every line that #144 and #145 edit is below `:426`, so neither moves #143's
README lines, and the hunks do not touch. The rebase in step 1 is expected to
be clean. It is still done, and the README hunks are re-checked by text
rather than by line number. The test count moves: #143's baseline after the
rebase is 1170 plus the tests #144 and #145 added.

## Steps

0. **Baseline.** `git status` is clean on `fix/143-stale-wtype-check`, and
   `git merge-base HEAD main` is `806804a`. `gh issue view 143` shows OPEN.
   With `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`, run
   `nix develop -c pytest tests -q`
   → verify by **1170 passed**. Record any failure that already exists before
   continuing.
1. **Precondition: #144 and #145 have merged.** `git fetch origin` and
   `git log --oneline 806804a..origin/main`. If both have merged,
   `git rebase origin/main`. If not, stop and ask the lead; #143 lands last.
   Repeat step 0 with the new count, and re-check every `file:line` in this
   plan (README hunks by text, see Landing order)
   → verify by a clean rebase and a green baseline, with the line numbers
   confirmed or corrected in this file, in the same commit as the code.
2. **`tests/test_doctor_hands.py` (new, `unittest`): write the tests and see
   them fail.** No `src/` change yet. Every test patches
   `omarchy_voice.cli.shutil.which` with `mock.patch`. None calls
   `cmd_doctor`, starts a process, or reads the mic, the compositor or the
   manifest.
   - `T1 test_no_wtype`: with `which` returning `None`, no line of
     `cli._hands_tools()` contains `wtype`.
   - `T2 test_each_tool_crossed_with_purpose`: with `which` returning
     `None`, there are exactly seven lines. For each `(tool, purpose)` in
     decision 3, exactly one line contains both `tool` and `purpose`, and
     that line contains `✗`.
   - `T3 test_each_tool_ticked_when_present`: with `which` returning
     `"/usr/bin/x"`, the same seven lines each contain `✓` and not `✗`.
   - `T4 test_helper_name_from_virtual_input`: with
     `virtual_input.HELPER` patched to `"fake-helper"`, `which` is called
     with `"fake-helper"` and never with `"ai-mirror-input"`, and the
     clicking line names `fake-helper`.
   - `T5 test_missing_tools_gone`: `hasattr(capabilities, "missing_tools")`
     is false.
   → verify by `nix develop -c pytest tests/test_doctor_hands.py -q` giving
   **5 failed** (T1 to T4 with `AttributeError` for `_hands_tools`, T5 on
   the assertion).
3. **`src/omarchy_voice/cli.py`: add `_hands_tools()` and use it.** Add
   `virtual_input` to the import at `:15-16` (ambiguity B). Add
   `_hands_tools()` after `_tick` (`:29-30`). It holds the seven pairs from
   decision 3, with `virtual_input.HELPER` as the fifth tool, and returns
   `[f"  {_tick(bool(shutil.which(t)))} {t} — {why}" for t, why in ...]`.
   Replace `:504-505` with `for line in _hands_tools(): print(line)`. Keep
   `print(_bold("\nhands"))` (`:503`) and everything from `:506` on.
   → verify by T1 to T4 passing, and T5 still failing.
4. **`src/omarchy_voice/capabilities.py`: delete `missing_tools()`**
   (`:1388-1389`) and the two blank lines after it (`:1390-1391`), so
   `unreadable_sources` keeps the two blank lines at `:1386-1387` above it. Keep `import shutil` (decision 2).
   → verify by 5 of 5 passing in `tests/test_doctor_hands.py`, and
   `grep -n 'shutil' src/omarchy_voice/capabilities.py` still printing
   `:29` and two uses.
5. **`nix/package.nix`, `flake.nix`: remove wtype** as in decision 8
   (`package.nix:7`, `:65-67` comment, `:69`; `flake.nix:62`, `:128`,
   `:141`).
   → verify by `nix develop -c true` exiting 0 (the dev shell still enters),
   and by `nix develop -c sh -c 'command -v wtype'` printing nothing unless
   wtype is installed on the host outside the shell (if it is, note that and
   rely on the `nix flake check` sandbox in step 8).
6. **`README.md`, `docs/omarchy-voice.html`: remove wtype** as in decision
   8 (`README.md:61`, `:219-220`, `:426`; `omarchy-voice.html:523`). The
   `README.md:219` paragraph is re-wrapped to the file's existing width.
   → verify by a rendered read of the three README passages and the HTML
   line.
7. **Grep: wtype and `missing_tools` are gone.**
   `git grep -n -i -e wtype -e missing_tools -- src nix flake.nix README.md docs`
   → verify by exactly four lines: `src/omarchy_voice/keys.py:217`,
   `src/omarchy_voice/tools.py:369`, `:3978`, `:3989` (decision 9,
   ambiguity D). There is no changelog in the repo, so there is no other
   exception.
8. **Full suite, build and flake check**, all with
   `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`:
   - `nix develop -c pytest tests -q` → **1175 passed** (1170 plus 5, or the
     rebased baseline plus 5).
   - `nix develop -c python3 -m unittest discover -s tests` → the same
     count, OK.
   - `nix build --no-write-lock-file .#omarchy-voice` → succeeds, and
     `grep -c wtype result/bin/omarchy-voice` prints `0`, while
     `grep -c ai-mirror-input result/bin/omarchy-voice` prints at least `1`.
   - `nix develop -c true` → exits 0.
   - `nix flake check --no-write-lock-file` → passes. It runs the whole
     suite in the sandbox without wtype on `PATH`, which is the check for
     the spec's first risk.
9. **Doctor by hand.** `nix run --no-write-lock-file .#omarchy-voice -- doctor`
   (or `result/bin/omarchy-voice doctor`)
   → verify by the hands section listing the seven tools with their
   purposes, no `wtype`, and the compositor-events lines unchanged below
   them. Nothing else in the output changes.
10. **Mutation checks** (below). Each is made by hand, the suite is run, and
    the change is reverted with `git checkout -- <file>`. None is committed.
11. **Commit** the code, tests and docs as
    `fix(doctor): check the tools the daemon runs, not wtype (#143)`, with
    this plan updated in the same commit if any step deviated.

## Tests

New: `tests/test_doctor_hands.py`, 5 tests (T1 to T5 in step 2), all failing
on `806804a` and passing after step 4.

Mutations (step 10). Each must turn at least the named test red:

| # | Mutation | Must fail |
| --- | --- | --- |
| M1 | put `("wtype", "typing")` back into `_hands_tools`'s list | T1, T2 (eight lines) |
| M2 | drop the `ai-mirror-input` pair | T2, T3, T4 |
| M3 | write `"ai-mirror-input"` as a literal instead of `virtual_input.HELPER` | T4 |
| M4 | tick every line `_tick(True)` regardless of `which` | T2 |
| M5 | drop `— {why}` from the line format | T2, T3 |
| M6 | restore `missing_tools()` in `capabilities.py` | T5 |
| M7 | put `pkgs.wtype` back at `flake.nix:62` | step 7 grep (not a test; the grep is the check) |

## Rollback

`git revert` the implementation commit. It touches only the seven files
above, adds no state, no config key and no on-disk format, so reverting
restores the `806804a` doctor output, `missing_tools()`, and wtype in the
package and dev shell exactly. Nothing needs to be cleaned on a host. A
rebuild after the revert puts wtype back on the wrapper's `PATH`.
