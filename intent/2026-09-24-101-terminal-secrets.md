---
status: approved
issue: 101
author: olafkfreund
---

# Intent: a tmux pane that just printed a secret goes to the model verbatim

Closes #101.

## Problem

`read_terminal` returns a pane's text **exactly**. That is its point, and it
is also the problem: nothing between `tmux capture-pane` and the model asks
what the text is. A pane that has just run `op read`, `cat .env` or
`pass show`, or has a private key on screen, is handed over as-is.

`read_screen` has had this question answered since #46: the sensitive-window
guard (`_sensitive_kind`, `tools.py:2258`), plus the lock, DPMS and recording
checks (#51, #52, #67) in `_screen_unavailable` (`tools.py:2601`). The terminal
path has no equivalent. And the policy gate never covered it: `describe`
(`tools.py:1842`) turns the call into `read terminal Work:1.1`, and that string
is all the gate ever sees. #100 exempting reads from confirm lost nothing here,
because there was nothing to lose.

**How the terminal path works today (origin/main, 93c6dac):**

- `_tmux_panes` (`tools.py:3524`) runs `tmux list-panes -a`: every pane in
  **every** session, attached or not. It is not limited to the `Work` session.
  On this desktop that is currently three sessions (`0`,
  `Local-Development`, `Synechron-Development`). Only the names and
  `pane_current_command` were listed for this intent, not the contents.
- `_resolve_pane` (`tools.py:3546`) picks by exact target, then by substring of
  target and title. An empty target picks a busy attached pane first.
- `_capture_pane` (`tools.py:3578`) is `capture-pane -p -J -S -<lines>`, capped
  at `TERMINAL_OUTPUT_LIMIT` = 6000 characters (`tools.py:391`). `lines` goes up
  to 2000.
- `_tool_read_terminal` (`tools.py:3598`) returns
  `"<target> (<idle|running>):\n" + text`.
- `_tool_list_terminals` (`tools.py:3609`) returns target, current command and
  the first 40 characters of `pane_title`. No contents.

**Four paths send pane contents to the model, not one.** Each calls
`_capture_pane`:

| Path | Where | What goes out |
| ---- | ----- | ------------- |
| `read_terminal` | `tools.py:3598` | up to 2000 lines / 6000 chars, on request |
| `run_in_terminal`, quick command | `tools.py:3715` | the default 200 lines, after the command finishes |
| `watch_terminal` on an idle pane | `tools.py:3744` | the last 40 lines, "what it last showed" |
| `poll_watches` (#74) | `tools.py:3800` | the last 30 lines as `tail`, **unprompted**: `watch_message` (`realtime.py:157`) puts it in the prompt verbatim, sent by `realtime.py:651` and `local_engine.py:532` |

The last one needs no request at all. A watched build that ends with a token on
screen gets announced, and the token is in the model's input before anyone has
spoken.

**Demonstrated with a fake tmux.** `FakeTmux` from `tests/test_terminal.py`
overrides `_shell`, so nothing touches the real tmux. The values are fake and
only the shapes are real. Each call went through `Executor.call`, so the policy
gate ran:

```
read_terminal, private key: ok=True verbatim=True   (-----BEGIN OPENSSH PRIVATE KEY----- …)
read_terminal, op read:     ok=True verbatim=True   (ghp_… on the line after `op read op://…`)
read_terminal, .env dump:   ok=True verbatim=True   (DATABASE_URL=…:hunter2@…, sk-…, AKIA…)
watch_message verbatim:     True                    (poll_watches → realtime.watch_message)
run_in_terminal verbatim:   True
watch_terminal (idle pane): True
gate sees:                  'read terminal Work:1.1'
```

**What there is to detect a secret with, and what each misses:**

1. **`pane_current_command`** (already fetched by `_tmux_panes`). It catches a
   secret command *while it is still running*: `op` at a prompt, `pinentry`,
   `gpg`, `ssh-keygen`, `age`, `pass` waiting on gpg-agent. It misses the case in
   the issue. `op read` and `pass show` exit in well under a second, and after
   that the pane reads `bash` and `idle`. Every live pane here reads `bash` right
   now. The command is gone and its output is still in the scrollback.
2. **`pane_title`** (already fetched). It is set by the shell or the program,
   and it usually shows the cwd or the last command. That makes it weaker than
   the window title was for #46, and like that title it must never be quoted in
   a refusal.
3. **Content shapes.** This is the only signal that survives the command
   exiting. Candidates:
   - PEM blocks: `-----BEGIN [A-Z ]*PRIVATE KEY-----`. Unambiguous.
   - Vendor token prefixes: `ghp_`/`gho_`/`ghs_`/`github_pat_`, `sk-`/`sk-ant-`,
     `xox[abp]-`, AWS `AKIA`/`ASIA` + 16. These are specific, so they rarely
     misfire.
   - The command line still on screen: `op read`, `pass show`, `cat .env`,
     `gpg -d`. The #100 secret-path list (`config.py:154-171`, the tail of
     `DEFAULT_DENY` at `config.py:132`) already matches `cat .env`, `~/.ssh`,
     `.password-store`, `.aws/credentials` and similar. **It matches paths, not
     secrets.** Tried against pane text, it hits `cat .env` and also a harmless
     `git log` that mentions `~/.ssh/config`. It does not hit the `op read`
     token or the PEM block at all.
   - `KEY=value` lines. On their own they are too broad (`DEBUG=True`). They
     might work when the key name contains `KEY|TOKEN|SECRET|PASSWORD` and the
     value is long.

**The false-positive risk is real and measured.** `DEFAULT_SENSITIVE`'s
`"a credential or one-time code"` pattern (`config.py:192`) is
`password|passcode|2fa|one-time|\botp\b`. Run over a pane showing
`def check(password: str)`, it matches. That pattern is fine for a window title.
For pane text it would withhold every editor, diff and test run that mentions a
password field, and on a developer's desktop that is a lot of them. The bare
word is not a usable signal for terminal content.

## Proposed outcome

- A pane whose contents look like a secret is not handed to the model by any of
  the four paths, including the unprompted watch announcement.
- The model is told why in terms it can act on. The reason names a *kind*
  ("a private key", "a token"), never the matched text and never the pane title.
- Ordinary terminal work, like code, diffs, test output and logs that merely
  mention passwords or `.env`, reads exactly as it does today.
- Whatever documents the guard says it is a heuristic. It is not a promise that
  the terminal is now safe.

## Affected users and systems

- Every user of `read_terminal`, `run_in_terminal` and `watch_terminal`, on both
  engines (realtime and local). This is the native tmux path.
- `src/omarchy_voice/tools.py`: `_capture_pane`, the four callers above, and
  possibly `_tool_list_terminals` (title).
- `src/omarchy_voice/realtime.py` `watch_message`, if the announcement has to say
  "finished, output withheld".
- `src/omarchy_voice/config.py`: `DEFAULT_DENY` / `DEFAULT_SENSITIVE`, if their
  patterns are reused or a new list sits beside them.
- `persona.py` / the tool description, if the model needs to know a read can be
  withheld and what to do instead: ask the user, not retry with more `lines`.

## Constraints

- **The refusal must not leak what it refused.** It says "a private key", not the
  key, and not the title. The same rule as `_sensitive_kind`.
- **It must not be keyed on `pane_current_command` alone.** The command has
  usually exited by the time anyone reads the pane (finding 1).
- **It must not use the bare word "password"** or similar words as a content
  signal. That is the false-positive case above.
- **No silent partial read.** If lines are dropped or redacted, the output says
  so. The model must not believe it saw the whole pane.
- **The guard sits at the shared point, not in each caller.** Four callers go
  through `_capture_pane`. Guarding only `read_terminal` leaves the unprompted
  announcement open, and that is the worst of the four.
- **No new dependency.** Regular expressions over text that is already in hand.
- **Tests use `FakeTmux`**, never the real tmux. Live panes are the user's own
  and may hold exactly the secrets this is about.

## Open questions

1. **Withhold the whole pane, or redact the matching lines?** Withholding is
   simple and honest, and it matches `read_screen`. It also blocks a whole
   200-line build log because one line had `AKIA…` in it. Redacting keeps the
   rest readable but has to find the *extent* of a secret: a PEM block is
   multi-line, and a token can wrap. A redacted `.env` still gives away its
   inventory of key names.
2. **Which signals?** Some options, from least to most false positives:
   (a) PEM blocks and vendor token prefixes only;
   (b) plus secret-printing command lines still on screen (`op read`,
   `pass show`, `cat .env`, reusing the #100 path list);
   (c) plus `pane_current_command` in {`op`, `pass`, `gpg`, `pinentry`,
   `ssh-keygen`, `age`} for the still-running case;
   (d) plus `KEY=value` with a secret-ish key name.
   Another option is to leave out (b) because it matches the *intent* to show a
   secret, which a `git log` also mentions.
3. **Are watch announcements in scope?** The recommendation is yes, since
   they are the unprompted path. The open part is whether a withheld tail still
   announces "finished" with no output, and so loses "did it work", or also
   says which kind was withheld.
4. **Is `run_in_terminal` output in scope?** The model's *own* `cat .env` is
   already stopped by the #100 deny list at the gate. The output of a harmless
   command that scrolls up past an earlier secret is not.
5. **Does it cover all sessions or just `Work`?** Today every session is
   readable, including detached ones such as `Synechron-Development` here. A
   narrower reach is a separate fix and might belong in its own issue.
6. **Can the list be configured?** Is it a fixed floor plus user additions,
   like `sensitive_patterns`, or a floor only?
