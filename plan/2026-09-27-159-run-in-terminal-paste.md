---
status: approved
issue: 159
spec: spec/2026-09-27-159-run-in-terminal-paste.md
---

# Plan: run_in_terminal delivers, reports and targets correctly

## Approved decisions (from the spec, complete)

1. **Delivery by bracketed paste.** `tmux load-buffer -b oma-<nonce> -` with
   the text on **stdin, never in argv**, then
   `paste-buffer -p -d -b oma-<nonce> -t <pane>`, then `send-keys -t <pane> Enter`.
   `send-keys` never carries the command. The model's command still may not
   contain a newline; the only pasted newline is the one before the marker.
2. **Exit marker** on a second pasted line, with a per-call nonce:
   `printf 'OMA_EXIT_<nonce>=%s\n' $?` for bash, zsh, sh, dash and ksh
   (verified live: bash, zsh).
   - fish, nu and elvish get no marker; their result says
     "exit status not checked in <shell>".
   - Read the last `OMA_EXIT_<nonce>=(\d+)` from the `_capture_pane` output,
     which is already secret-guarded (#101).
   - Non-zero → `ok=False`, "exit <n>".
   - The marker line is stripped from the output returned; it stays visible in
     the pane.
   - Watched commands keep the nonce, and `poll_watches` adds `"exit": n`.
3. **Drawn sessions.** `_drawn_sessions() -> set[str]`:
   - read `tmux list-clients -F '#{client_pid}\t#{session_name}'`;
   - walk `/proc/<pid>/status` `PPid:` up, at most 32 steps, stopping at pid ≤ 1;
   - a session is drawn if an ancestor is the pid of a Hyprland client on a
     visible workspace.

   This replaces `attached and _terminal_on_screen()` at the three
   run_in_terminal sites (`tools.py:4177`, `4188`, `4214`). `TERMINAL_CLASSES`
   stays for `tools.py:2173`.
   *Deviation (found in live step 6):* with no target, the command runs in
   Oma's own tmux session, `Oma`, never "the first idle pane of a drawn
   session". Omarchy's `launch terminal tmux` is `tmux attach || tmux new -s
   Work`, and a bare `attach` joins the most recently used session: on p620 it
   opened a new window on `Synechron-Development`, and then on
   `Local-Development`, so the visibility check was satisfied while the
   command still landed in a work session. The terminal is now opened with
   `omarchy-launch-terminal tmux new-session -A -s Oma`, and the user's
   sessions are used only as a named target, which must be drawn. The
   tool's description says so.
   *Deviation (live step 6, second run):* the first paste into a freshly
   opened session was echoed by a shell still starting up and never run, and
   was reported "ran; exit status not shown", ok. Two changes: after opening
   the terminal, wait until the pane has drawn and stayed unchanged for 0.6 s
   (at most 8 s), instead of a fixed 0.5 s; and a marker that was expected but
   is absent is now `ok=False` ("may not have run"), not a success.
   *Deviation (live step 6, third run):* that made a slow first command a
   false failure: the new shell read the paste over half a second after the
   0.6 s grace, then ran it with exit 0. For shells with a marker, the marker
   is now the finish line: poll until this call's marker appears (up to
   `TERMINAL_QUICK_WAIT`), and only an idle pane with no marker at the end of
   the wait is "may not have run". Shells without a marker keep busy/idle.
4. **Scope:** `run_in_terminal`, its pane choice, and `poll_watches`. No other
   tool sends text into a pane.
5. **Known ceilings** (from the spec's risks, accepted):
   - single-process multi-window terminals share a pid;
   - a shell with bracketed paste disabled falls back to today's behaviour;
   - fish, nu and elvish get no marker until tested.

## Steps

Each step is one commit on `fix/159-run-in-terminal-paste`, with its tests.

1. **`_shell`/`_spawn` accept `input: str | None`** (`tools.py:2333`,
   `2350`): `Popen(..., stdin=PIPE if input is not None else None)` and
   `communicate(input=...)`. `_tmux(*args, input=None)` passes it through.
   → Verify: the existing suite is unchanged, and a new test shows
   `_spawn(["cat"], input="x")` returns "x".

2. **Paste delivery** in `_tool_run_in_terminal` (`tools.py:4229`):
   - nonce `secrets.token_hex(4)`;
   - build `text = command + "\n" + marker` (marker per shell, or none);
   - call `load-buffer` with `input=text`, then `paste-buffer -p -d`, then `Enter`;
   - a failure at any of the three returns that failure.

   `FakeTmux` records `input`.

   → Verify: `tests/test_terminal.py` covers:
   - the exact text reaches `load-buffer`'s stdin;
   - `paste-buffer` has `-p` and `-d`;
   - no `send-keys` carries the command;
   - the fish pane gets no marker.

   Mutation: restoring `send-keys` with the command fails a test.

3. **Exit status**: `_exit_from(capture, nonce) -> (int | None, str)` returns
   the last match and the output with the marker lines removed. In the finish
   branch: non-zero → `Result(False, f"exit {n} in {pane}:\n{out}")`; `None`
   with a marker expected → ok, noting the status wasn't seen; a shell without
   a marker → "exit status not checked in <shell>".

   → Verify, with tests:
   - 0 → ok;
   - 2 → not ok, "exit 2";
   - another nonce's marker is ignored;
   - the marker line is stripped;
   - the echoed input line (`%s`) never matches.

   Mutation: dropping the nonce from the regex fails the stale-marker test.

4. **Watched commands**: `watch(..., nonce=None)` stores it, and
   `poll_watches` reads the marker for a finished watch, adding `"exit"`. The
   daemon's announcement (`local_engine.py`, where finished watches are
   spoken) says "failed with exit n" when it is non-zero.

   → Verify:
   - a `poll_watches` test with a finished pane and a marker of 3 gives `exit == 3`;
   - `pytest tests/test_local_engine.py -q` is green, with one new test for
     the spoken wording.

5. **Drawn sessions**: add `_drawn_sessions()`; `_ensure_visible_session` and
   the explicit-target check use it; the post-launch wait waits for the new
   session to be drawn.

   `FakeTmux` gains `drawn=` (default: the attached sessions when
   `terminal_visible`, else none), so the existing tests keep their meaning.

   → Verify, with new tests:
   - ancestry reaches a visible window → drawn;
   - the terminal is on a hidden workspace → not drawn;
   - a window of an unlisted class counts;
   - the walk stops at 32 steps and at pid 1;
   - an attached session that is not drawn is not used, and a terminal is
     opened instead.

   → Plus the full suite.

6. **Live on p620.**
   - In a throwaway server (`tmux -L oma159`), through the real Executor via
     `omarchy-voice say` with `--dry-run` off:
     - the #159 command runs intact;
     - `false` → failure.
   - Then on the real desktop, announced on the agent bus first:
     - a tmux session attached only on a hidden workspace is not used;
     - #158's live check 6 (`dev-setup` end to end, pointed at a scratch
       directory without `--continue`) passes.
   - Record the results here, and post them on the PR and on #158.

   *Recorded (step 6, 2026-09-27, p620):*
   - Throwaway tmux server, real Executor, the user's real bash with its
     auto-pairing prompt: the #159 command delivered intact (`got [x b]`);
     `false` → `exit 1`, failure; `ls /nonexistent-oma` → `exit 2`,
     failure; no marker in any output handed back.
   - Real desktop, first runs: found that Omarchy's terminal launcher
     attaches to the last-used session (twice a work session), then a first
     paste lost to a shell still starting, then a slow first command read too
     early. Each fixed and recorded above.
   - Final run: with no tmux client, a terminal opened on the `Oma` session,
     the first command in the fresh shell ran and read `exit 0`; with that
     terminal hidden (its workspace switched away on the same monitor) a
     second `Oma` terminal was opened on the visible workspace instead of
     using it; none of the user's sessions was touched.
   - #158 live check 6: `dev-setup` (scratch dir, no `--continue`), run
     with the clean environment a routine or the menu has: all four steps
     ok; herdr made the `dev` workspace, Claude started there and received
     the prompt verbatim, quotes intact.
   - Test harness note: running `omarchy-voice` from a shell inside herdr
     leaks `HERDR_*` into the launched terminal and herdr refuses to nest;
     the menu, voice and timers do not carry those variables.
   - Everything the tests made was removed; the daemon is back on the
     deployed build and the menu file is byte-identical.

## Tests

- `nix develop -c pytest tests -q`: everything green. The count grows by the
  new tests.
- `nix flake check`: all checks pass (the sandbox runs the suite too).
- Mutation checks as listed in steps 2 and 3.

## Rollback

Revert the merge. The change is confined to `tools.py` (`_shell`/`_spawn`
input, `run_in_terminal`, `_drawn_sessions`, the watch) and one line of
announcement wording in `local_engine.py`. Nothing on disk or in the user's
config is written by it; the marker lines already in scrollback are inert text.
