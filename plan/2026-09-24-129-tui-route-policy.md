---
status: approved
issue: 129
spec: spec/2026-09-24-129-tui-route-policy.md
---

# Plan: a program started by the tui route answers to the same policy as any other launch

Closes #129. Branch `fix/129-tui-route-policy`, based on main `50dcf99`.
Every `file:line` below was re-read on `50dcf99`. `src/`, `tests/` and
`README.md` do not differ from `50dcf99` on this branch. Where the spec's
line numbers drifted, the plan uses the verified ones (listed under
"Line corrections").

This is a safety change to the policy gate. It lands **after** #77, #83 and
#91 (see "Landing order"). Step 1 rebases and re-locates every line.

## Approved decisions, carried over from the spec

1. **One program is checked as one set of texts, whichever route starts it,
   in both directions** (spec Q1). The texts are:
   - `launch <program>`, where `<program>` is the basename of the binary
     that runs;
   - `launch <id>` for every installed desktop entry whose `Exec=` runs that
     binary.

   The tui route (`omarchy launch tui`, `launch-or-focus tui`, a compose
   `tui` pane) knows the binary. It adds `launch <binary>` and looks up the
   ids. `launch_app` and a compose `app` pane know the id. They keep #110's
   `launch <id>` and add `launch <binary>` from the entry. After this,
   `^launch dev\.zed\.Zed`, `^launch zeditor\b` and `\bzeditor\b` each stop
   Zed on every route.
2. **Every id counts, and deny wins across all texts** (spec Q1). If several
   entries run the same binary, every one of their ids is checked. The
   deny-only pass runs over every text before any confirm check, so a
   confirm match on one text cannot hold a call that another text denies.
3. **With the shell off, a short list of programs that take commands is
   held** (spec Q2, option b). The list is a `frozenset` constant
   `_COMMAND_PROGRAMS` beside `_BARE_PROGRAM_RE` (`tools.py:391`):

   ```
   sh bash dash zsh fish ksh mksh csh tcsh nu elvish xonsh
   python ipython bpython pypy node deno bun perl ruby irb php lua luajit
   tclsh wish julia ghci guile racket sbcl r
   tmux screen zellij script nix-shell su doas run0
   ```

   These are shells, REPL interpreters, multiplexers, and `su`/`doas`/`run0`.
   `btop` and `lazygit` stay free (#112's rows). Editors and pagers are not
   on the list. A held bare name falls through to the existing hold reason:
   `an omarchy launcher given a command` on the omarchy route, and
   `a compose pane runs a command` on a pane. `describe` labels such a pane
   `name (tui: bash)`, #112's label for a pane that runs a command.
   Options (a) and (c) were rejected.
4. **A trailing version number is stripped before the lookup.**
   `_takes_commands(n) = re.sub(r"[\d.]+$", "", os.path.basename(n).lower()) in _COMMAND_PROGRAMS`,
   so `python3`, `python3.12` and `lua5.4` are caught.
5. **Built-in rules do not change. A tui program is not matched as a
   command line** (spec Q3). The new text is `launch <basename>`, with no
   arguments. The existing description already carries the arguments, so
   `rm-rf`, `dd` and the other rules that need arguments still match there.
   A bare `rm` runs. `ssh` as a tui stays denied by the word rule and can be
   dropped by name (`deny_patterns_remove = ["ssh"]`, #109).
6. **The hold happens up front, at the front gate** (spec Q4). The new texts
   go into `launches` in `Executor._call_locked` (`tools.py:1884`, list at
   `:1890`), so #110's deny-first pass (`:1919-1922`) and confirm pass
   (`:1923-1925`) cover them. A confirm match on any text holds the whole
   call under its front description, before a workspace switch or a launch.
   Dry run (`:1955`) runs after the gate, so it is covered too.
7. **The handler's per-pane check gains the same texts, and its wording
   stays** (spec Q4). In `_tool_compose_windows` (`tools.py:3578`), the
   per-pane `texts` (`:3607-3609`) gain the pane's texts: `_app_texts` for
   an `app` pane and `_program_texts` for a `tui` pane. The deny-only pass
   (`:3613-3614`) and the full pass (`:3615-3616`) run over them. The
   `Denied` branch (`:3617-3618`) and the `NeedsConfirmation` branch
   (`:3619-3623`) are unchanged. A confirm match passes only while
   `_releasing` is set (`:3622`). `_releasing` is set by `run_pending` at
   `:1984` and cleared at `:1990`. Outside a release, a confirm match there
   returns `pane N (…) is not allowed by policy`.
8. **Which program an `Exec=` runs** (spec Design §1). This is a change to
   `capabilities.app_index` (`capabilities.py:488`), at the parse on
   `:527` and `:533`:
   - `import shlex` is added (between `re` `:27` and `shutil` `:28`);
   - `shlex.split` is used, falling back to `str.split` on `ValueError`;
   - a leading `env` and any `NAME=value` words are skipped.

   So `Exec=env GDK_BACKEND=x11 foo %U` gives `foo`, and a quoted path gives
   no quote. `find_apps` (`:590`) uses `command` as an identity word, and
   this only improves its matches.
9. **Three helpers beside `_launch_text` (`tools.py:723-727`), stdlib only,
   no new type** (spec Design §2):
   - `_program_texts(program)`: `launch <basename>` plus `launch <id>` for
     every `app_index()` row with `command == basename`, de-duplicated in
     order;
   - `_app_texts(app)`: `_launch_text(app)` plus `launch <command>` of the
     row whose id is the bare id (`:action` dropped), when it has one;
   - `_tui_program(words)`: the first word of `shlex.split(" ".join(words))`
     that does not start with `-`, or `""`.

   Each helper reads `capabilities.app_index()` once. It is a file read of
   `app_dirs()`, with no launch and no compositor query, so it is safe in
   dry run and in `_validate_compose_windows`. It is not cached (the
   `ponytail:` note at `capabilities.py:494-495` stands). On the omarchy
   route, the words come from `normalise_omarchy` (`:1713`), split the way
   `omarchy_runs_command` splits them (`:1755`). The helpers apply when
   those words start with `launch tui` or `launch or focus tui`. On the
   pane route, the words are `shlex.split(target)`. A target that cannot be
   split is left for `_validate_compose_windows` (`:3540`) to refuse, as it
   is today.
10. **The README sentence is rewritten in this PR** (spec Q5, Design §6).
    This is #110's sentence in Safety (`README.md:820-824`). It is replaced
    by the spec's text, which is quoted in step 9.
11. **Unchanged:**
    - `describe`, except the #112 label for a newly held pane;
    - `Policy.check`;
    - #109's rule names;
    - #97/#70 resolution;
    - dry run;
    - `run_pending`;
    - the default deny and confirm lists;
    - `_is_terminal_window` (`:2015-2021`) and the keyboard hold (`:2040-2045`).
12. **Approved with (2026-09-25): two behaviour changes are accepted, and
    the README says so.**
    - An app whose `Exec=` starts with `pkexec`, such as GParted
      (`Exec=pkexec /usr/bin/gparted`), is now refused through `launch_app`
      and an `app` pane by the built-in `pkexec` rule, because of the text
      `launch pkexec`.
    - A deny rule on one Flatpak app's id also refuses a bare
      `launch tui flatpak`, because every Flatpak entry runs `flatpak`.
13. **Approved with (2026-09-25): the typing gap is out of scope.** A typed
    Return into a bare `vim` or `less` tui window, whose class is not a
    terminal class, is not held. This is tracked as **#145** against #112's
    keyboard gate. This plan does not touch `_is_terminal_window` or
    `TERMINAL_CLASSES`.

### Plan-level resolutions, flagged for this gate

**R1. An empty program has no texts.** The spec's `_program_texts` would
turn `""` into `launch ` and, worse, into every id whose `Exec=` is missing,
because `app_index` stores `command: ""` for those rows (`capabilities.py:533`).
So a bare `omarchy launch tui` could be refused by an unrelated app's rule.
Resolution: `_program_texts` returns `[]` when the basename is empty. This
is one guard line, and it matches `_app_texts`, which already skips an empty
`command`.

**R2. The shell-off tests stop reading the host's apps.** `tests/_isolated.py`
moves `XDG_DATA_HOME` but not `XDG_DATA_DIRS`, so `app_dirs()` still reads
the host's `/run/current-system/sw/share/applications`. After this change,
every tui row in `tests/test_shell_off.py` consults `app_index()`, and a
host entry could change a row's result. Resolution: `FakeDesktop.setUp`
(`tests/test_shell_off.py:73-83`) also patches `omarchy_voice.tools.app_dirs`
and `omarchy_voice.capabilities.app_dirs` to an empty temp dir, as
`ComposeFakes` does (`tests/test_compose.py:303-305`).

**R3. "The README says so" is read as an instruction.** The spec's README
text (Design §6) does not mention `pkexec` or Flatpak, but the approval note
says the README does. Resolution: step 9 adds one sentence naming both.

**R4. The multiple-id test uses its own fixture.** If a second entry that
runs `zeditor` were in the shared fixture, `launch_app zeditor` would become
ambiguous under #70 and refuse for the wrong reason. So the multiple-id test
adds `dev.zed.ZedPreview.desktop` inside the test and checks the tui routes
only.

### Line corrections (spec → verified on `50dcf99`)

| Spec | Verified |
| --- | --- |
| `_call_locked` `:1886-1911` | `:1884`; `launches` `:1888-1912` |
| deny-first `:1917-1922` | `:1919-1922`, confirm pass `:1923-1925` |
| dry run `:1937` | `:1955` |
| `omarchy_runs_command` bare case `:1763-1765` | `:1761-1763` (function `:1746-1772`) |
| keyboard hold `:2040-2044` | `:2040-2045` |

All other spec citations match: `capabilities.py:527-534`, `:590`,
`tools.py:391`, `:686`, `:715-720`, `:723`, `:1713`, `:1754-1755`,
`:1984-1990`, `:2015-2021`, `:3607-3609`, `:3620-3623`, `README.md:820-824`.

### Line corrections after step 1 (2026-09-25, rebased on main `fffebfa`)

#77, #83, #91, #128 and #139 have all merged; none is pending. Every anchor
was re-found by symbol, once each, on `fffebfa`. Step 0 on the rebased head:
**1149** tests OK in both runners (`git diff fffebfa -- src tests README.md`
empty). Numbers below are before this plan's edits.

| Plan (`50dcf99`) | `fffebfa` |
| --- | --- |
| `_BARE_PROGRAM_RE` `tools.py:391` | `:393` |
| `_pane_runs_command` `:715-720` | `:717-722` |
| `_launch_text` `:723-727` | `:725-729` |
| `normalise_omarchy` `:1713` | `:1763` |
| `omarchy_runs_command` `:1746-1772`, split `:1755`, bare case `:1761-1763` | `:1796-1822`, `:1805`, `:1811-1813` |
| `class Executor` `:1805` | `:1881` (#77's constants at `:1859`) |
| `_call_locked` `:1884`, `launches` `:1888-1912` | `:1958`, `:1962-1988` |
| `launches.append(_launch_text(…))` `:1898`, `:1910` | `:1972`, `:1984` |
| deny-first `:1919-1922`, confirm pass `:1923-1925` | `:1995-1999` |
| hold returns `confirm_instruction` `:1951` | `:2025` |
| dry run `:1955` | `:2029` |
| `run_pending` `_releasing` set `:1984`, cleared `:1990` | `:2058`, `:2064` |
| `_is_terminal_window` `:2015-2021` | `:2089` |
| `_validate_compose_windows` `:3540` | `:3716` |
| handler `texts` `:3607-3609` | `:3783-3785` |
| deny-only / full pass `:3613-3616` | `:3789-3792` |
| `Denied` / `NeedsConfirmation` `:3617-3623` | `:3793-3799` |
| `capabilities.py` `import re` `:27`, `Exec` `:527`, `:533`, `find_apps` `:590` | `:27`, `:527`, `:533`, `:572` |
| `README.md:820-824` | `:842-846` |

`grep -n "needs spoken confirmation"` over the diff of `tests/test_policy.py`
and `tests/test_shell_off.py` lists no new line: every hold is compared to
`ex.confirm_instruction`.

**Red on unchanged code (step 2).** 103 failures in pytest (subtests
counted) and in unittest (`failures=102, errors=1`; the error is
`test_the_texts`, whose helpers do not exist yet). Every guard passed.

### Deviations found while implementing (2026-09-25)

- **D1. `_tui_program` falls back to `str.split` on a `ValueError`.** The
  plan does not say what an unsplittable omarchy tui line does. Its words
  come from `normalise_omarchy`, and re-joining them can leave a lone quote
  (`launch tui "it's"`); an exception there would break the call before the
  gate. The fallback is the one `app_index` uses for `Exec=`, so the program
  is still checked. The pane route keeps the plan's shape: a `ValueError`
  from `shlex.split(target)` leaves the pane without texts, and the
  validator refuses it.
- **D2. The handler's `app` pane uses `texts.extend(_app_texts(target))`**
  in place of keeping `texts.append(_launch_text(target))` and adding
  `_app_texts(target)[1:]`. The texts are the same list; the lookup is one
  call, not two.
- **D3. Step 8's comment is written in step 6's commit.** It is the comment
  above `launches`, which step 6 edits.
- **D4. The step-2 and step-11 test command.**
  `python3 -m unittest tests.test_policy …` cannot import `_isolated` on
  main (`ModuleNotFoundError`), before or after this change. The modules are
  run by name from `tests/`
  (`cd tests && python3 -m unittest test_policy test_shell_off test_find_apps test_compose`),
  which is what `discover -s tests` does.
- **D5. The `ssh` guard reads its config through `config.load`.**
  `deny_patterns_remove` is applied in `load` (#109), not by `Config(...)`,
  so the test writes `[hands] deny_patterns_remove = ["ssh"]` to a temp
  file and loads it.
- **D6. `tests/_isolated.py` sets `XDG_DATA_DIRS` to an empty
  `ROOT/share`** (the "Approved with" addition), rather than unsetting it:
  unset, `app_dirs()` falls back to `/usr/share`. The test is
  `test_isolation.py::test_a_host_xdg_data_dirs_does_not_reach_a_test`,
  which checks in a fresh interpreter that every `app_dirs()` path is under
  `ROOT`. Step 11 gains a row for it.

## Steps

0. **Baseline.** `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`.
   Run `nix develop -c python3 -m unittest discover -s tests` and
   `nix develop -c python3 -m pytest -q tests`. Then
   `git diff 50dcf99 --stat -- src tests README.md`.
   → verify: both runners report **1085** tests OK on `50dcf99`, and the
   diff is empty.
1. **Rebase onto whatever has merged, then re-locate every `file:line`.**
   Run `git fetch origin` and `gh pr list --state merged --search "#77"`,
   and the same for `#83` and `#91`. Rebase this branch onto `origin/main`.
   Then re-find every anchor in this plan by symbol, not by number:
   - `_BARE_PROGRAM_RE`, `_pane_runs_command`, `_launch_text`;
   - `omarchy_runs_command`, `normalise_omarchy`;
   - `def _call_locked`, `launches: list[str]`,
     `for text in (description, *launches)`;
   - `def run_pending`, `self._releasing`;
   - `texts = [" ".join(argv)]` in `_tool_compose_windows`;
   - the `command = fields.get("Exec"` line in `capabilities.py`;
   - `governed by .allow_shell` in `README.md`.

   Rerun step 0 on the rebased head and record the new count.

   Any #77, #83 or #91 that has **not** merged is named in the PR as still
   pending. This plan still goes last, so that branch rebases on this one.
   Every corrected line number is written into this file, in the same
   commit as the first code change.
   → verify: every anchor is found once; the Line corrections table is
   updated to match; both runners pass on the rebased head, with the count
   recorded.
2. **Tests first, written before any code change** (see Tests):
   - `tests/test_policy.py`: a new class `TuiRoutePolicyTests` at the end
     of the file;
   - `tests/test_shell_off.py`: rows for bare programs, and the R2 patch
     in `FakeDesktop.setUp`;
   - `tests/test_find_apps.py`: a new class `ExecParseTests`.

   → verify: run
   `python3 -m unittest tests.test_policy tests.test_shell_off tests.test_find_apps`
   on the unchanged code. Every test marked **(red on main)** fails, and
   every **(guard)** passes. Record the failing count in the PR.
3. **`src/omarchy_voice/capabilities.py:27` and `:527`: the `Exec=` parse**
   (decision 8). Add `import shlex`. Replace `:527` with the following:
   `shlex.split`, `except ValueError:` `str.split`, then drop a leading
   `env` and then any leading words that match `^[A-Za-z_][A-Za-z0-9_]*=`.
   `:533` is unchanged.
   → verify: `ExecParseTests` pass, and `tests/test_find_apps.py` passes
   unchanged.
4. **`tools.py:391`, `:715-720`, `:1761-1763`: the shell-off list**
   (decisions 3 and 4).
   - Add `_COMMAND_PROGRAMS` and `_takes_commands` after `_BARE_PROGRAM_RE`,
     with the `ponytail:` comment from the spec ("a fixed list, not a
     classification; if a new REPL reaches the log, add it here").
   - In `_pane_runs_command`, `:720` becomes
     `kind == "tui" and (not _BARE_PROGRAM_RE.match(target) or _takes_commands(target))`.
   - In the `launch tui` / `launch or focus tui` case guard (`:1762`), add
     `and not _takes_commands(n)`.

   → verify: the bare-program table in `tests/test_shell_off.py` passes, and
   the existing `TABLE` (`tests/test_shell_off.py:117-156`) still passes.
5. **`tools.py`, after `_launch_text` (`:727`): `_tui_program`,
   `_program_texts` and `_app_texts`** (decisions 1, 2 and 9, and R1). Use
   the spec's code, with R1's `if not name: return []` in `_program_texts`.
   → verify: the helper tests in `TuiRoutePolicyTests` pass:
   - `_program_texts("/usr/bin/zeditor") == ["launch zeditor", "launch dev.zed.Zed"]`;
   - `_program_texts("") == []`;
   - `_app_texts("dev.zed.Zed:new-window") == ["launch dev.zed.Zed:new-window", "launch zeditor"]`;
   - `_app_texts("gparted")` ends with `"launch pkexec"`;
   - `_tui_program(["zeditor ."]) == "zeditor"`.

   The end-to-end tests still fail.
6. **`tools.py:1891-1912`: the front gate** (decisions 1, 6 and 12).
   - `:1898`: `launches.append(_launch_text(…))` becomes
     `launches.extend(_app_texts(…))`.
   - `:1910`: the same change.
   - In the compose pane loop (`:1902-1911`), a dict pane with
     `kind == "tui"` extends `launches` with
     `_program_texts(_tui_program(shlex.split(target)))`. A `ValueError`
     leaves the pane without texts; the validator refuses it later.
   - A new branch for `name == "omarchy_cli"`: take
     `argv = normalise_omarchy(str(args.get("command", "")))[0]` and, when
     `argv` is not empty, split it into `words` as at `:1755`. If `words`
     starts with `["launch", "tui"]`, extend `launches` with
     `_program_texts(_tui_program(words[2:]))`. If it starts with
     `["launch", "or", "focus", "tui"]`, use `words[4:]`.

   The passes at `:1919-1925` are not edited.
   → verify: every front-gate row of the route table, the deny-first
   tests, the reverse-lookup tests and the Flatpak test pass.
7. **`tools.py:3607-3609`: the per-pane check** (decision 7). `texts` gains
   `_app_texts(target)[1:]` for an `app` pane, whose first text is already
   `_launch_text`, and `_program_texts(_tui_program(shlex.split(target)))`
   for a `tui` pane. The `try` and both `except` branches are not edited.
   → verify: the handler-level tests pass, and so does
   `tests/test_compose.py` unchanged.
8. **Docstrings.** Update the comment above `launches` (`:1888-1889`) to
   say "every launch, on every route, is also checked as `launch <id>` and
   `launch <program>` (#110, #129)".
   → verify: `grep -n "#129" src/omarchy_voice/tools.py` lists the new
   comment, `_COMMAND_PROGRAMS` and the three helpers.
9. **`README.md:820-824`** (decisions 10 and 12, R3). Replace the two
   sentences beginning "An installed app is checked as" with the spec's
   paragraph:

   > A program is checked as `launch <desktop-id>` and as `launch <program>`
   > (the binary its entry's `Exec=` runs), whether `launch_app`, a
   > `compose_windows` `app` or `tui` pane, or `omarchy launch tui` /
   > `launch-or-focus tui` starts it. So `^launch dev\.zed\.Zed`,
   > `^launch zeditor\b` or `\bzeditor\b` in `deny_patterns` stops Zed on
   > every one of them. With `allow_shell` off, a `tui` program that is a
   > shell, an interpreter or a multiplexer waits for a yes. A terminal,
   > `run_in_terminal`, `launch-or-focus <pattern> <cmd>` and a program
   > wrapped in another command are governed by `allow_shell`, not by this
   > rule.

   After it, add one sentence (R3):

   > So an entry whose `Exec=` starts with a denied word is refused too (an
   > `Exec=pkexec …` entry such as GParted, by `pkexec`), and a rule on one
   > Flatpak app's id also refuses a bare `launch tui flatpak`.

   → verify: `grep -n 'launch <program>' README.md` finds one line;
   `grep -n 'pkexec' README.md` finds the new sentence; and
   `grep -n 'governed by .allow_shell., not by this rule' README.md` finds
   one line.
10. **Full run and flake.** Run both runners, then
    `nix flake check --no-write-lock-file`.
    → verify: the step-1 count plus the new tests pass, with the same count
    in both runners, and the flake check is green.
11. **Mutation checks.** Apply each mutation in the table below. Run
    `python3 -m unittest tests.test_policy tests.test_shell_off tests.test_find_apps tests.test_compose`,
    then revert.
    → verify: every row is caught, and `git diff` after the revert shows
    only the intended change.

## Tests

Fakes only. Nothing is spawned, no real app is launched, and nothing writes
the real log (#99). Every file imports `_isolated` first. Every run has
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`.

**Fixture for `TuiRoutePolicyTests`.** A temp dir is patched into
`omarchy_voice.tools.app_dirs` and `omarchy_voice.capabilities.app_dirs`,
and `XDG_CURRENT_DESKTOP=Hyprland` is set. The dir holds:
- `dev.zed.Zed.desktop` (`Name=Zed`, `Exec=zeditor %U`);
- `btop.desktop` (`Exec=btop`);
- `gparted.desktop` (`Exec=pkexec /usr/bin/gparted`);
- `env.desktop` (`Name=EnvApp`, `Exec=env GDK_BACKEND=x11 envapp`);
- `org.example.App.desktop` (`Name=Example`,
  `Exec=flatpak run org.example.App`);
- `noexec.desktop` (`Name=NoExec`, no `Exec=`).

The rest of the fixture:
- `shutil.which` is faked as in `tests/test_shell_off.py:36-43`, with the
  same `ON_PATH`;
- `_shell` records argv into `self.ran`;
- `_dispatch_lua` records into `self.lua`;
- `_query_rows` returns fixed windows, and `_wait_tick` is a no-op, as in
  `ComposeFakes` (`tests/test_compose.py:316-331`).

"Refused" means:
- `ok` is false;
- `ran == []` and `lua == []` (no workspace switch);
- one `DENIED` line;
- `pending is None`.

"Held" means:
- `pending` is set;
- `result.output == ex.confirm_instruction` (the attribute, never a literal;
  see #77 below);
- `ran == []` and `lua == []`.

Then `run_pending()` makes `ran` non-empty.

**Route × rule × shell table**
(`test_route_rule_shell_table`, one `subTest` per cell: 8 × 6 × 2 = 96).

Routes:

| # | Call |
| --- | --- |
| R1 | `omarchy_cli "launch tui zeditor"` |
| R2 | `omarchy_cli "launch-or-focus tui zeditor"` |
| R3 | compose `[tui zeditor, web https://example.com/]` on workspace 4 |
| R4 | `launch_app "zeditor"` (resolved to `dev.zed.Zed`, #70) |
| R5 | `launch_app "dev.zed.Zed"` |
| R6 | compose `[app dev.zed.Zed, web https://example.com/]` on workspace 4 |
| R7 | `omarchy_cli "launch tui 'zeditor .'"` |
| R8 | `omarchy_cli "launch tui /usr/bin/zeditor"` |

Expected results:

| Rule | shell on | shell off |
| --- | --- | --- |
| none | RAN, all | RAN R1-R6; **HELD** R7, R8 (`allow_shell is off`) |
| deny `\bzeditor\b` | REFUSED, all | REFUSED, all |
| deny `^launch dev\.zed\.Zed` | REFUSED, all | REFUSED, all |
| deny `^launch zeditor\b` | REFUSED, all | REFUSED, all |
| confirm `^launch dev\.zed\.Zed` | HELD, all, then runs | HELD, all, then runs |
| confirm `^launch zeditor\b` | HELD, all, then runs | HELD, all, then runs |

Red on main:
- deny `^launch dev\.zed\.Zed` × R1, R2, R3, R7 and R8;
- deny `^launch zeditor\b` × all eight;
- deny `\bzeditor\b` × R4, R5 and R6;
- the two confirm rows on the same cells, except R7 and R8 with the shell
  off, which main already holds.

Every other cell is a guard.

**Deny before confirm across the new texts**
- **(red on main)** deny `^launch zeditor\b` + confirm
  `^launch dev\.zed\.Zed`:
  - R1 is refused, not held;
  - R5 is refused, not held (the confirm text is the id, and the deny text
    is the reverse `launch zeditor`).
- **(red on main)** the same pair at the handler, with `_releasing = True`,
  on a resolved tui pane `zeditor` returns `not allowed by policy`, and
  `ran == []`.

**Several ids for one binary (R4)**
- **(red on main)** The test writes `dev.zed.ZedPreview.desktop`
  (`Exec=/opt/zed/bin/zeditor --preview`). Deny
  `^launch dev\.zed\.ZedPreview` refuses R1 and R3.

**Reverse lookup, the env skip, and the accepted changes (decision 12)**
- **(red on main)** With `Config()` defaults, `launch_app "gparted"` and a
  compose `app` pane `gparted` are refused with
  ``blocked by deny rule `pkexec` ``.
- **(red on main)** Deny `^launch envapp\b` refuses `launch_app "env"`.
- **(red on main)** Deny `^launch org\.example\.App` refuses
  `omarchy_cli "launch tui flatpak"`, with the shell on and off.
- **(guard)** With defaults, `launch tui flatpak` runs.

**Not a command line (decision 5)**
- **(red on main)** Deny `^launch zeditor$` refuses R7, `'zeditor .'`
  (the text is `launch zeditor`, with no arguments).
- **(guard)** With defaults, `launch tui rm` runs.
- **(guard)** `launch tui ssh` is refused with ``blocked by deny rule `ssh` ``.
  With `deny_patterns_remove=["ssh"]` and the shell on, it runs.

**Empty program (R1)**
- **(guard)** Deny `^launch noexec` with the shell on:
  `omarchy_cli "launch tui"` runs, and `_program_texts("") == []`.

**Handler level (decision 7).** `_tool_compose_windows` is called directly
(no front gate) with a resolved tui pane `zeditor`.
- **(red on main)** Under deny `^launch dev\.zed\.Zed` it returns
  `pane 1 (zeditor) is not allowed by policy`, and `ran == []`.
- **(red on main)** Under confirm `^launch dev\.zed\.Zed`, not releasing, it
  returns the same refusal.
- **(guard)** Under confirm `^launch dev\.zed\.Zed` with `_releasing = True`,
  it launches.
- **(red on main)** An `app` pane `gparted` under defaults is refused in the
  handler.

**Bare-program table** (`tests/test_shell_off.py`, `BARE_TABLE`). Each
program is tried via `launch tui <p>`, `launch or focus tui <p>` and a
compose `tui` pane `<p>`. Results are (shell off, shell on):
- **(guard)** `btop`, `lazygit`: RAN, RAN.
- **(red on main)** `bash`, `sh`, `zsh`, `fish`, `nu`, `python3`,
  `python3.12`, `node`, `tmux`, `su`: HELD, RAN. The compose `HOLD` line
  contains `(tui: bash)` for `bash`.
- **(guard)** `rm`: RAN, RAN.
- **(guard)** `passwd`, `sudo`, `ssh`: REFUSED, REFUSED.

**`ExecParseTests`** (`tests/test_find_apps.py`). `app_index()` over a temp
dir:
- `Exec=zeditor %U` gives `zeditor`;
- **(red on main)** `Exec="/opt/My App/bin/qapp" %U` gives `qapp`;
- **(red on main)** `Exec=env GDK_BACKEND=x11 envapp %U` gives `envapp`;
- `Exec=bad "quote` gives `bad` and does not raise (the fallback).

Existing tests must pass unchanged:
- `tests/test_shell_off.py` `TABLE`;
- `tests/test_compose.py`, including `OneRuleEveryLaunchTests`;
- `tests/test_policy.py`.

Commands, after `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`:

- `nix develop -c python3 -m unittest discover -s tests`: `OK`, the step-1
  count plus the new tests.
- `nix develop -c python3 -m pytest -q tests`: the same count passing.
- `nix flake check --no-write-lock-file`: green.

### Mutations (step 11), one per decision

| Decision | Mutation | Test that must fail |
| --- | --- | --- |
| 1 (tui → id) | drop the `omarchy_cli` branch from `launches` | table: `^launch dev\.zed\.Zed` × R1, R2, R7, R8 |
| 1 (id → binary) | `_app_texts` returns `[_launch_text(app)]` | table: `\bzeditor\b` × R4-R6 |
| 1 (basename) | drop `os.path.basename` in `_program_texts` | table: `^launch zeditor\b` × R8 |
| 2 (every id) | `ids[:1]` in `_program_texts` | several ids for one binary |
| 2 (deny wins) | swap the deny-only and confirm passes at the front | deny before confirm, R1 and R5 |
| 3 (omarchy) | drop `_takes_commands` from the case guard | bare table: `launch tui bash` rows |
| 3 (pane) | drop `_takes_commands` from `_pane_runs_command` | bare table: compose `bash` rows |
| 3 (narrow) | add `btop` to `_COMMAND_PROGRAMS` | bare table: `btop` rows |
| 4 | drop the version strip | bare table: `python3.12` |
| 5 | `_program_texts` emits `launch <program> <args>` | `^launch zeditor$` × R7 |
| 6 | drop compose tui texts from the front (keep the handler) | table: R3 confirm cells (`lua == []`, `pending` set) |
| 7 (texts) | drop the tui texts from the handler's `texts` | handler deny |
| 7 (`_releasing`) | drop `if not self._releasing` (always pass) | handler confirm, not releasing |
| 8 (shlex) | revert to `str.split` | `qapp` |
| 8 (fallback) | drop the `ValueError` fallback | `bad "quote` |
| 8 (env) | drop the `env` / `NAME=` skip | `launch_app "env"`, `envapp` |
| 9 / R1 | drop `if not name: return []` | empty program |
| 10 | revert the README hunk | step 9's greps |
| 12 | skip rows whose command is `pkexec` or `flatpak` | `gparted` tests and the Flatpak test |
| Approved with (D6) | drop the `XDG_DATA_DIRS` line from `tests/_isolated.py` | `test_a_host_xdg_data_dirs_does_not_reach_a_test` |

That is one or more row for each changeable decision. Decisions 11 and 13 are
"no change". The existing suite and the guards above hold them, and
`git diff` must show no edit to `_is_terminal_window`, `TERMINAL_CLASSES`,
`Policy`, `describe` or `run_pending`.

## Rollback

`git revert` the implementation commit. It removes the code, the tests and
the README hunk together. There is no config key, schema, persisted state,
cache format or NixOS module change. The `app_index` parse change only
changes `command` values, which are not stored.

Without a revert, a user surprised by a refusal can loosen their own
`^launch …` rule. GParted's refusal comes from the built-in `pkexec` rule,
which was accepted (decision 12). Do not advise removing that rule.

## Landing order and overlap

**Order: #77, #83, #91, then #129 last.** All four edit
`src/omarchy_voice/tools.py`. Step 1 rebases onto whatever has merged and
re-locates every line.

- **#77** (`refactor/77-engine-duplication`, plan approved `174ee97`):
  - It inserts three module constants just above `class Executor`
    (`tools.py:1805`), and `Executor.__init__` (`:1816-1818`) assigns
    `SPOKEN_HOLD_INSTRUCTION` in place of the literal.
  - Every #129 hunk after `:1805` shifts by the block's size (about +15):
    - the front gate `:1884-1925`;
    - the handler `:3607-3609`.
  - The hunks at `:391`, `:715-727` and `:1746-1772` do not move.
  - The hold this plan's confirm tests reach returns `self.confirm_instruction`
    (`:1951`), whose text #77 moves into `SPOKEN_HOLD_INSTRUCTION`. So no
    #129 test compares the hold wording to a literal. They assert
    `result.output == ex.confirm_instruction`.
  - Step 1 checks this: `grep -n "needs spoken confirmation" tests/test_policy.py tests/test_shell_off.py`
    lists no new line.
  - #77's plan expects #129 at `:391`, `:715-727`, `:1746-1772` and
    `:1890-1925`. That is correct.
- **#83** (`feat/83-services-and-mcp`, plan approved `70a7d57`):
  - `find_service` joins `READ_ONLY_TOOLS` (`tools.py:58-60`). A read-only
    call gets no `launches`, so the gate's behaviour for it is unchanged.
  - Its other `tools.py` hunks are the schemas (`:884-900`, `:1342-1354`,
    `:1492-1495`), `describe` (`:2104-2105`) and a handler after
    `_tool_find_command` (`:2439-2462`). It cites the #112 hold at `:1916`
    inside `_call_locked` but does not edit it.
  - In `capabilities.py`, it adds functions near `:800` and edits the
    manifest (`:1119-1125`). This plan edits the import block and
    `:527-533`, so the hunks are separate. Both change
    `capabilities.py`'s hash, which is harmless (#103 `_cache_key`).
  - In `tests/test_policy.py`, it edits `READ_FAKES` (`:674`). This plan
    appends a class at the end of the file, so there is no shared line.
- **#91** (`feat/91-atspi-app-content`, plan approved `d01bec0`):
  - It adds `_tree_nodes` beside `_windows_in` (`tools.py:2709`) and a tree
    attempt inside `_ocr_region` (`:2814`) and `_ocr_words` (`:2919`).
    These are on the `read_screen` path (`_read_screen_text` `:3312`), and
    one clause is added to `read_screen`'s description (`:1104-1112`).
  - `read_screen` is read-only and gets no `launches`. #129 changes nothing
    on that path, and #91 changes nothing in the gate.
  - Its insertions come before this plan's handler hunk (`:3607-3609`),
    which shifts. There is no shared line.
  - #91 adds `src/omarchy_voice/a11y.py`, which this plan does not touch.

Other work that lands after this one rebases on it. Its implementer should
know that `launches` now carries tui and reverse texts.


## Approved with (2026-09-25)

The owner approved this plan together with one addition: `tests/_isolated.py` also isolates `XDG_DATA_DIRS`, pointing it at a throwaway directory, so no test reads the host's installed apps. This goes beside #83's `CLAUDE_CONFIG_DIR` reset in the same file; whichever lands second rebases. It needs a test that a host `XDG_DATA_DIRS` does not reach a test. The per-test `app_dirs` patch in R2 stays.
