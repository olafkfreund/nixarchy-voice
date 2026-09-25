---
status: approved
issue: 129
intent: intent/2026-09-24-129-tui-route-policy.md
---

# Spec: a program started by the tui route answers to the same policy as any other launch

Closes #129. Line numbers are from `fix/129-tui-route-policy` at `dd67561`,
whose code is main `50dcf99`. The evidence is the intent's measured route
table. It was not re-run for this spec. The code it cites was re-read at
`dd67561` and has not changed.

## Decisions on the intent's open questions

The intent was approved with its questions unanswered, so each one is decided
here and can be rejected at this gate.

1. **Both, and in both directions.** Whichever route starts a program, the
   gate checks it as one set of texts:
   - `launch <program>`, where `<program>` is the basename of the binary that
     runs;
   - `launch <id>` for every installed desktop entry whose `Exec=` runs that
     binary.

   The tui route knows the binary, so it adds `launch <binary>` and looks up
   the ids. `launch_app` and an `app` pane know the id, so they add
   `launch <id>` (#110, unchanged) and look up the binary. After that,
   `^launch dev\.zed\.Zed`, `^launch zeditor\b` and `\bzeditor\b` each stop
   Zed on all six routes in the intent's table. Reason: the intent measured
   the gap in both directions. Closing only one direction would leave a rule
   that works on some routes and not others, which is what this issue
   reports. If several entries run the same binary, every one of their ids is
   checked. Deny wins over confirm across all of the texts, so a rule on any
   one of those ids applies. That is the conservative reading of "the same
   program".
2. **(b): with the shell off, hold a short list of programs that take
   commands.** `bash`, `python3` and `nu` are held as "runs a command", even
   though they are bare names. `btop` and `lazygit` stay free (#112's rows).
   Reason for rejecting (a): with the shell off, the only thing that makes
   typing risky is the keyboard hold, "presses Return in a terminal"
   (`_runs_command`, `tools.py:2040-2044`). That hold decides whether a
   window is a terminal by its class (`_is_terminal_window`, `:2015-2021`,
   `TERMINAL_CLASSES`). A tui window is launched with its own app id, for
   example `bash` for a compose pane (`_tui_app_id`, `:686`), and that id
   contains no terminal word. So a bare `bash` tui is a shell prompt that the
   model can type a line into with no yes. `launch_app foot` also opens a
   shell, but its window is recognised as a terminal. Reason for rejecting
   (c): it costs a yes for every `btop`, and #112 approved those rows
   deliberately. The list is kept narrow on purpose:
   - shells;
   - REPL interpreters;
   - multiplexers, which open a shell;
   - `su`, `doas` and `run0`, which open a root shell and which no deny rule
     names.

   Programs that can shell out, such as editors and pagers, are not on it
   (see Risks).
3. **Built-in rules: no change. A tui program is not matched as a command
   line.** Every tui text already carries the whole target. The description
   is `omarchy launch tui rm -rf ~`, and a pane label with arguments is
   `name (tui: rm -rf ~)`. So `rm-rf`, `dd` and the other rules that need
   arguments match as soon as there are arguments. With the shell off, any
   argument also means a hold. A bare `rm` exits with "missing operand" and
   runs nothing. Treating it as a command line would add a special case to
   `Policy` for no gain. `ssh` as a tui stays denied. The rule is a word
   rule and was chosen that way. A user who wants interactive ssh can drop it
   by name (`deny_patterns_remove = ["ssh"]`, #109). The new `launch <program>`
   text keeps the program's name as a word, so it gives the same word-rule
   results as today and no new ones. The one exception, the reverse lookup,
   is covered under Risks.
4. **Hold up front, at the front gate, and keep the handler's wording.** The
   new texts go into `launches` in `_call_locked` (`tools.py:1886-1911`),
   so #110's deny-first pass (`:1917-1922`) covers them. A confirm match on
   any of them holds the whole call under its front description, before a
   workspace switch or a launch. That is what #110 did for app panes. For a
   compose tui pane, the handler's per-pane `texts` (`:3607-3609`) gain the
   same texts, so the inner check is no weaker than the outer one (a
   constraint). A confirm match there passes only while `_releasing` is set
   (`:3620-3623`, set by `run_pending` at `:1984-1990`). The handler checks
   the same texts that the front gate held on, so outside a release the
   handler reaches a confirm match only if the installed apps changed between
   the two checks. In that case "not allowed by policy" is the correct
   fail-closed answer, and its wording stays.
5. **README: rewrite #110's sentence here** (Design §5). The current sentence
   says a `tui` pane is "governed by `allow_shell`", and with a bare name
   that is false. The design is settled in this spec, so the sentence is
   rewritten in the same PR as the change.

## Design

The change uses the stdlib only and adds no new type. It touches
`src/omarchy_voice/tools.py`, one line of the `app_index` parse in
`src/omarchy_voice/capabilities.py`, and one README paragraph.

1. **Which program an `Exec=` runs** (`capabilities.py:527-534`). `app_index`
   already stores `command`, the basename of the first word of `Exec=`. The
   module gains `import shlex`. The
   parse is changed to use `shlex.split`, falling back to `str.split` on a
   `ValueError`, so a quoted path does not leave a quote in the name. It also
   skips a leading `env` and any `NAME=value` words, so
   `Exec=env GDK_BACKEND=x11 foo %U` gives `foo`, not `env`. `find_apps`
   (`:590`) uses `command` as an identity word, so this only improves the
   matches there.

2. **The texts, in one place** (`tools.py`, beside `_launch_text`, `:723`).
   Two module functions. Each reads `capabilities.app_index()` once, which is
   a file read of `app_dirs()`. It launches nothing and queries no
   compositor, so it is safe in dry run and in `_validate_compose_windows`.

   ```python
   def _program_texts(program: str) -> list[str]:
       """A tui program as the gate sees it: its binary and every id whose
       Exec= runs it (#129)."""
       name = os.path.basename(program)
       ids = [r["id"] for r in capabilities.app_index() if r["command"] == name]
       return list(dict.fromkeys([f"launch {name}", *(f"launch {i}" for i in ids)]))

   def _app_texts(app: str) -> list[str]:
       """An app launch as the gate sees it: #110's `launch <id>` plus the
       binary its entry runs (#129)."""
       text = _launch_text(app)
       bare = text.removeprefix("launch ").partition(":")[0]
       exe = next((r["command"] for r in capabilities.app_index()
                   if r["id"] == bare and r["command"]), "")
       return list(dict.fromkeys([text, *([f"launch {exe}"] if exe else [])]))
   ```

   `_tui_program(words)` gets the program from a tui target. It takes the
   first word that does not start with `-` from `shlex.split(" ".join(words))`
   and returns `""` when there is none. The pane path passes
   `shlex.split(target)`. The omarchy path passes the words after `tui`.

3. **The front gate** (`_call_locked`, `:1893-1911`). Each call site that
   appended `_launch_text(...)` now extends `launches` with
   `_app_texts(...)`. Two cases are added:
   - `omarchy_cli`: normalise the command (`normalise_omarchy`, `:1713`) and
     split the family into words the same way `omarchy_runs_command` does
     (`:1754`). If the words start with `launch tui` or
     `launch or focus tui`, extend `launches` with
     `_program_texts(_tui_program(rest))`.
   - `compose_windows`: each `tui` pane extends `launches` with
     `_program_texts(_tui_program(shlex.split(target)))`. A target that
     cannot be split is left for `_validate_compose_windows` to refuse, as
     it is today.

   After that, the existing loop runs deny-first over
   `(description, *launches)` and then confirm, unchanged.

4. **The per-pane check** (`_tool_compose_windows`, `:3607-3609`). An `app`
   pane now adds `_app_texts(target)`, and a `tui` pane adds its
   `_program_texts(...)`. The `Denied`, `NeedsConfirmation` and `_releasing`
   branches are unchanged.

5. **The hold with the shell off** (`tools.py:391`). A constant
   `_COMMAND_PROGRAMS` (frozenset) goes beside `_BARE_PROGRAM_RE`:

   ```
   sh bash dash zsh fish ksh mksh csh tcsh nu elvish xonsh
   python ipython bpython pypy node deno bun perl ruby irb php lua luajit
   tclsh wish julia ghci guile racket sbcl r
   tmux screen zellij script nix-shell su doas run0
   ```

   The helper
   `_takes_commands(n) = re.sub(r"[\d.]+$", "", os.path.basename(n).lower()) in _COMMAND_PROGRAMS`
   strips a trailing version number, so `python3.12` and `lua5.4` are
   caught. The bare-name case in `omarchy_runs_command` (`:1763-1765`) gains
   `and not _takes_commands(n)`, so a bare shell falls through to the
   existing hold reason. `_pane_runs_command` (`:715-720`) becomes
   `kind == "tui" and (not _BARE_PROGRAM_RE.match(target) or _takes_commands(target))`.
   As a result, `describe` labels such a pane `name (tui: bash)`, which is
   #112's label for a pane that runs a command.
   # ponytail: a fixed list, not a classification. If a new REPL reaches
   # the log, add it here.

6. **README** (Safety, `README.md:820-824`). The sentence becomes:

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

The following do not change: `describe` (except the #112 label for a newly
held pane), `Policy.check`, the rule names (#109), #97/#70 resolution,
dry run (the front gate runs before it, at `:1937`), and `run_pending`.

## Alternatives rejected

- **Check the tui program only as `launch <binary>`.** Rejected: an id rule
  such as `^launch dev\.zed\.Zed`, which is the shape the #110 README
  teaches, would still miss the tui route. That is the issue itself.
- **Check `launch_app` only by id, with the reverse lookup left out.**
  Rejected by Q1: `\bzeditor\b` would stop the tui route and not
  `launch_app`, so the gap would close in one direction only.
- **Hold every tui launch with the shell off, option (c).** Rejected by Q2.
  It removes #112's approved free rows.
- **Treat tui windows as terminals in `_is_terminal_window`.** This would
  hold the typed Return into any tui window, not only a shell's. It is a
  change to the keyboard route (#112 decision 6) with its own trade-offs,
  since it would hold Return in `lazygit` too, so it is out of this scope.
  See Risks.
- **Match the tui program against the deny list as if it were a command
  line.** Rejected by Q3: the text already carries the arguments.
- **Read the entry file directly for the id → binary lookup** instead of
  `app_index`. Rejected for now: it adds a second `[Desktop Entry]` parser.
  The cost is the hidden-entry gap under Risks.

## Risks

- **A call that used to run is now refused or held.** This happens only
  with a rule that already stops the same program on another route, which
  is the point of the change. There is one case with the default rules: an
  entry whose `Exec=` starts with a denied word, for example
  `Exec=pkexec /usr/bin/gparted`. `launch_app` of it now gets the text
  `launch pkexec` and is refused by `pkexec`. This is accepted. The entry
  asks for root, and the deny list says never.
- **Over-matching through a wrapper binary.** Flatpak entries all run
  `flatpak`. So a deny on one flatpak app's id also refuses a bare
  `launch tui flatpak`, which only prints help. This is accepted as harmless.
- **Residual: hidden entries.** `app_index` skips `NoDisplay`, `Hidden` and
  `OnlyShowIn`-excluded entries. `launch_app` of such an id by its exact
  name is still caught by an id rule, but not by a binary rule. A tui
  launch of its binary is caught by a binary rule, but not by its id.
- **Residual: a wrapped program.** `launch tui env zeditor`, or a pane
  `sh -c zeditor`, is checked as `launch env` / `launch sh`. With the shell
  off, both are held: the first is not a bare name, and `sh` is on the
  list. With the shell on, only a rule on the text (`\bzeditor\b`) stops
  them. The README says so.
- **Residual, larger, not in #129's scope: a typed Return into a tui window
  is not held.** A bare `vim` or `less` tui runs free (Q2). Its window class
  is not a terminal class, so `type_text ":!rm x\n"` into it passes the
  keyboard hold with the shell off. This spec narrows the gap to programs
  that are not shells and does not close it. It needs its own issue against
  #112's keyboard gate.
- **Cost:** one `app_index` scan (about 36 ms per 300 entries) per tui or
  app call, and per compose call at each of the two gates. That is small
  beside a launch.
- Hosts: all. The lookup reads `app_dirs()`, so the id set depends on what
  is installed on the host.

## Verification

Fakes only. Nothing is spawned. Every run uses
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` and `tests/_isolated.py`.
The fixture app dir, patched into `tools.app_dirs` and
`capabilities.app_dirs`, holds:
- `dev.zed.Zed.desktop` (`Exec=zeditor %U`);
- `btop.desktop` (`Exec=btop`);
- `gparted.desktop` (`Exec=pkexec /usr/bin/gparted`);
- `env.desktop` (`Exec=env GDK_BACKEND=x11 envapp`).

`shutil.which` is faked as in `tests/test_shell_off.py`, and launches are
recorded, not run.

**Route × rule × shell table** (`tests/test_policy.py`, one `subTest` per
cell). Routes: the six Zed routes from the intent's table, plus
`launch tui 'zeditor .'` and `launch tui /usr/bin/zeditor`. Rules: none;
deny `\bzeditor\b`, `^launch dev\.zed\.Zed`, `^launch zeditor\b`; confirm
`^launch dev\.zed\.Zed`, `^launch zeditor\b`. Both shell settings.
Expected results:
- With no rule, every route runs. `'zeditor .'` and `/usr/bin/zeditor` are
  held with the shell off.
- Every deny rule refuses every route in both shell settings, with a
  `DENIED` line and nothing launched and no workspace switch.
- Every confirm rule holds every route, with `pending` set and nothing
  launched, and `run_pending()` then launches it.

On main, the id and binary columns fail on the rows the intent marked in
bold.

**Bare-program table** (`tests/test_shell_off.py`). These run unheld with
the shell off and with it on:
- `btop` and `lazygit` via `launch tui`, `launch or focus tui` and a
  compose pane.

These are held with the shell off and run with it on:
- `bash`, `sh`, `zsh`, `fish`, `nu`, `python3`, `python3.12`, `node`,
  `tmux` and `su`.

These give the same result in both settings:
- `rm` runs.
- `passwd`, `sudo` and `ssh` are refused by name.

**Deny before confirm across the new texts:** deny `^launch zeditor\b`
plus confirm `^launch dev\.zed\.Zed` refuses `launch tui zeditor`. It does
not hold it.

**Reverse lookup:** `launch_app gparted` is refused by the `pkexec` rule,
and `launch_app env` is refused by deny `^launch envapp\b` (the env-skip).

**Handler level:** `_tool_compose_windows` is called directly with a
resolved tui pane `zeditor`:
- Under deny `^launch dev\.zed\.Zed` it returns `not allowed by policy`
  with `launched == []`.
- Under confirm, outside a release, it gives the same refusal.
- Under confirm with `_releasing = True`, it launches.

**Mutation checks.** Each one must turn a named test red:
- Drop the tui texts from the front `launches`: the table's id column goes
  red.
- Drop them from the handler's `texts`: the handler-level deny goes red.
- Drop the reverse lookup in `_app_texts`: the `\bzeditor\b` ×
  `launch_app` cells and the `gparted` test go red.
- Drop `_takes_commands` from `omarchy_runs_command` or from
  `_pane_runs_command`: the matching shell-off rows go red.
- Add `btop` to `_COMMAND_PROGRAMS`: the free rows go red.
- Drop the version strip: `python3.12` goes red.
- Swap the two loops to confirm-first: the deny-before-confirm test goes
  red.

All green:
- `nix develop -c pytest tests -q`
- `nix develop -c python3 -m unittest discover -s tests`
- `nix flake check --no-write-lock-file`


## Approved with (2026-09-25)

The owner approved this spec with its two behaviour changes accepted: an app whose `Exec=` starts with `pkexec` (e.g. GParted) is now refused by the built-in `pkexec` rule, and a deny rule on one Flatpak app also refuses a bare `launch tui flatpak`. The README says so. The typing gap (Return into a bare `vim` or `less` tui) is tracked as its own issue against #112's keyboard gate.
