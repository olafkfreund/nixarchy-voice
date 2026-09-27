---
status: draft
issue: 159
intent: intent/2026-09-27-159-run-in-terminal-paste.md
---

# Spec: run_in_terminal delivers, reports and targets correctly

The intent's three answers, taken at approval: bracketed paste, a visible
exit marker, and the pane-visibility fix in this issue. Line numbers are on
`main` at `647f9e5`.

## Evidence the design rests on

Measured on p620 in a throwaway tmux server (`tmux -L oma159`), running the
user's real interactive bash, so the user's own sessions were never touched:

```
command:    p=$(echo "a b" | tr a x) && echo "got [$p]"   # trailing comment

send-keys:  ❯ p=$(echo )"a "b" "| tr a x) && echo "got "[$p]" "  # trailing comment
            bash: syntax error near unexpected token `)'

paste -p:   got [x b]
            OMA_EXIT=0          (and OMA_EXIT=1 for `false # fail`)
```

Bash is covered above. For zsh (`zsh -f`), the paste delivered `echo "got [(a b)]" # c`
intact, with `OMA_EXIT=0` and `=1`. fish and nu are not installed on p620 and
were not tested.

## Design

Only one place sends text into a pane: `_tool_run_in_terminal`
(`tools.py:4229`). `compose_windows` starts panes with a command as argv, not
by typing, and `send_shortcut` goes through the virtual keyboard, not tmux.
The change is therefore confined to `run_in_terminal`, its pane choice, and
the watch that finishes it.

### 1. Deliver by bracketed paste

Replace `send-keys -- <command> Enter` with:

```
tmux load-buffer -b oma-<nonce> -        # text on stdin, never in argv
tmux paste-buffer -p -d -b oma-<nonce> -t <pane>
tmux send-keys -t <pane> Enter
```

- `-p` wraps the text in bracketed-paste sequences, and readline, ble.sh and
  zle take it literally, so auto-pairing never sees a keystroke.
- `-d` deletes the buffer, so nothing lingers in tmux's paste list.
- The buffer is named per call, so it cannot collide with the user's own.
- `_tmux` gains an `input=` parameter for `load-buffer`'s stdin. Today it
  takes argv only.
- The model's `command` still may not contain a newline (`_validate_run_in_terminal`,
  `tools.py:4196`, unchanged). The only newline pasted is the one added before
  the marker line (below).

### 2. Read the exit status from a marker line

After the command, on a second pasted line (so a trailing `# comment` in the
command cannot swallow it), the shell prints a marker with a per-call nonce:

| Pane's shell (`pane_current_command`) | Marker line |
|---|---|
| bash, zsh, sh, dash, ksh | `printf 'OMA_EXIT_<nonce>=%s\n' $?` |
| fish, nu, elvish | none: untested here; the result says "exit status not checked in <shell>" |

- Reading: `re.findall(rf"OMA_EXIT_{nonce}=(\d+)", capture)`, last match. The
  echoed input line contains `%s`, not a digit, so it never matches. The nonce
  means a marker left in scrollback by an earlier call is never read as this
  one's.
- **Non-zero → `Result(False, "exit <n>: <output>")`.** An action runner, and
  the model, then stop and say so.
- The marker line is removed from the output handed back to the model: it is
  plumbing, not output. It stays visible in the user's pane, which was
  accepted at approval.
- **A watched command** (still running after `TERMINAL_QUICK_WAIT`): the
  watch remembers the nonce, and `poll_watches` reads the marker when the pane
  goes idle, adding `"exit": n` to the finished record. The daemon's
  announcement says "finished" or "failed with exit n" instead of only
  "finished".
- It runs after the secret guard: the marker is looked for in the text
  `_capture_pane` returns (#101), so no extra, unfiltered capture is taken.

### 3. Run only in a pane that is actually drawn

Today `_ensure_visible_session` (`tools.py:4176-4179`) accepts the first idle
pane of any session with *a* tmux client, provided *some* terminal-class
window is on a visible workspace. These are independent facts. On p620 the
visible terminal was herdr's, and the command went to a work session the user
was not looking at.

New helper, `_drawn_sessions() -> set[str]`:

1. `tmux list-clients -F '#{client_pid}\t#{session_name}'`.
2. For each client, walk `/proc/<pid>/status` `PPid:` upward, at most 32
   steps. The tmux client is a descendant of the terminal emulator drawing it.
3. If any ancestor pid is the `pid` of a Hyprland client on a visible
   workspace (`_visible_workspaces()`, `hyprctl clients -j`), that session is
   drawn.

This replaces the `attached and _terminal_on_screen()` test at all three
sites (`tools.py:4177`, `4188`, `4214`):

- **No target:** the first idle pane of a drawn session. If there is none,
  open one (`omarchy launch terminal tmux`, as now) and wait for its session to
  become drawn.
- **Explicit target:** its session must be drawn, or the call is refused with
  the existing "not on screen" message.

No window-class list is involved any more, so a terminal Oma has never heard
of (herdr's `org.omarchy.herdr`, which `TERMINAL_CLASSES` misses) is judged by
what the compositor shows. `TERMINAL_CLASSES` stays for its other use
(`tools.py:2173`).

## Alternatives rejected

- **`send-keys -l`** (literal): still delivered key by key, so the line editor
  still auto-pairs. It fixes tmux's key-name parsing, which was never the bug.
- **Appending `; printf … $?` on the same line:** a trailing `# comment`
  in the command turns the marker into a comment. Shown to be a real shape: the
  test command above ends in one.
- **`tmux wait-for` plus a status file:** invisible, but it writes a file per
  command and needs the shell to signal tmux. More moving parts for the same
  number.
- **Matching tmux clients to windows by title or class:** titles are
  user-set, and the class of a tmux-hosting terminal says nothing about which
  session it shows. Process ancestry is what the kernel knows.
- **Refusing commands with brackets or quotes:** refuses the use case.

## Risks

- **A single-process, multi-window terminal** (kitty single-instance, a foot
  server, ghostty with one process): every window shares one pid, so a tmux
  client in a window on a hidden workspace counts as drawn if a sibling window
  is visible. This is no worse than today, where any terminal on screen
  qualified. It's a known ceiling for those terminals; Omarchy's default,
  alacritty launched per window, has one process per window.
- **Shells without a marker** (fish, nu, elvish): the command is delivered
  correctly, but success is not claimed. The result says the status was not
  checked. Add markers when each is tested.
- **A shell with bracketed paste turned off** (`bind 'set enable-bracketed-paste off'`):
  the paste arrives as plain keys, with today's behaviour. The command is
  still delivered, just exposed to auto-pairing again. Acceptable: that user
  opted out of the protection.
- **The marker in the user's scrollback:** visible by design, one line per
  command.
- **Behaviour change for callers:** a command that exits non-zero now returns
  `ok=False`. The model sees the failure instead of a success, which is the
  point. Tests asserting `ok` on a failing command change with it.

## Verification

- Unit tests (`tests/test_terminal.py`, fakes as today):
  - paste path: `load-buffer` gets the exact text on stdin; `paste-buffer -p -d`
    and a lone `Enter` follow; `send-keys` never carries the command.
  - marker: exit 0 → ok; exit 2 → `ok=False` with "exit 2"; a stale marker with
    another nonce is ignored; the marker line is stripped from the output;
    fish → "not checked".
  - drawn sessions: a client whose ancestry reaches a visible window's pid is
    drawn; one whose terminal is on a hidden workspace is not; a
    non-terminal-class window counts; the ancestry walk stops at 32 steps and
    at pid 1.
  - watch: `poll_watches` reports `exit` from the marker.
- Mutation checks: reverting to `send-keys`, or dropping the nonce, must fail a
  test.
- Live on p620, in the throwaway tmux server first, then for real:
  - the #159 command runs intact;
  - `false` reports failure;
  - with a tmux session attached only on a hidden workspace, a no-target call
    opens a terminal instead of using it;
  - #158's live check 6 (`dev-setup` end to end) passes.
