---
status: approved
issue: 172
intent: intent/2026-09-27-172-dev-setup-first-run.md
---

# Spec: dev-setup works the first time a new user runs it

## Decisions on the intent's open questions

The intent was approved on 2026-09-27 ("continue") with its questions
unanswered, so the recommendations are taken:

1. **No earlier session → a fresh Claude**, in the same pane, told that it is
   starting fresh.
2. **An untrusted folder → stop with one clear line**, leaving the pane on
   Claude's trust dialog for the user to answer. Nothing answers it for them.
3. **The workspace step goes.** The file says it focuses herdr, which is
   what happens.
4. **The prompt says "the other desktop"**, with a comment saying where to
   name your own host.

## What herdr does, measured on p620 (2026-09-27)

`herdr agent start <name> --kind claude --pane <p> [--timeout MS] -- <args>`
succeeds only once Claude is ready for input. Its `--help` says so, and it
was measured:

| Case | Result |
| --- | --- |
| Trusted repo with an earlier interactive session, `--continue` | `agent_status: idle` after **3.9 s**; `agent prompt` lands |
| Trusted repo, no interactive session, `--continue` | Claude prints "No conversation found to continue" and exits; herdr waits out the timeout: `{"error":{"code":"timeout"}}`, rc 1; **the pane is back at its shell prompt** |
| A folder not yet trusted | `{"error":{"code":"agent_not_ready","message":"… blocked during startup …"}}`, rc 1; Claude is waiting on its trust dialog in the pane |

A `claude -p` run does not count as a session to continue. The case is
decided from herdr's error code. Claude Code's own files (transcripts,
`~/.claude.json`) are not read: they are internal, and `~/.claude.json` is on
Oma's secret-path deny list (#144).

## Design

Only `src/omarchy_voice/examples/dev-setup.toml` and its docs change. No
Python changes.

### Steps

1. **`omarchy_cli launch terminal-herdr`**, unchanged. It focuses herdr, or
   opens it.
2. **`run_in_terminal`**, rewritten. It is still one command:
   `_validate_run_in_terminal` refuses newlines (`tools.py:4425`). The file
   writes it as a TOML multi-line basic string with line-ending `\`, so it
   reads as lines but parses to a single line:

   ```sh
   ( repo=~/src/my-project; \
     p=$(herdr workspace create --cwd "$repo" --label dev --focus | jq -r .result.root_pane.pane_id) || exit 1; \
     out=$(herdr agent start dev --kind claude --pane "$p" --timeout 15000 -- --continue); \
     case $(printf %s "$out" | jq -r '.error.code // "ok"') in \
       ok) hello="Continue where we left off." ;; \
       timeout) herdr agent start dev --kind claude --pane "$p" >/dev/null || exit 1; \
                hello="This is a new session in this repository: there was nothing to continue." ;; \
       agent_not_ready) echo "Claude is asking whether to trust $repo. Answer it in the herdr pane, then run dev setup again."; exit 1 ;; \
       *) printf '%s\n' "$out"; exit 1 ;; \
     esac; \
     herdr agent prompt "$p" "$hello When something needs a UI test, drive the other desktop through the ai-mirror MCP, or the browser through the Chrome MCP, and tell me what you saw." )
   ```

   - **The whole thing runs in a subshell `( … )`,** so `exit 1` ends only
     the subshell. It never closes the shell of Oma's tmux session. The exit
     marker (#159) then reads that status, so a stop reads as a failed step,
     and the `echo` line is what Oma reports.
   - **15 s for `--continue`** is about 4× the measured 3.9 s. When there is
     nothing to continue, a first run pays that wait once. Later runs don't.
   - **If `--continue` times out on a slow resume, not a missing one,** the
     fresh start fails, because the pane is not at its prompt. The step then
     stops with herdr's error. This is a known ceiling, marked with a
     `ponytail:` comment. The upgrade path is a longer timeout.
   - POSIX `sh` syntax only (`case`, `$( )`, subshell), which runs in bash,
     zsh, sh, dash and ksh: the shells #159's exit marker supports.
3. **`open_page` GitHub notifications**, unchanged.

The old step 1 (`hypr_dispatch focus workspace 4`) is removed.

### Comments in the file

- The header says what happens: herdr is focused or opened, a herdr
  workspace is created in your repository, Claude resumes there (or starts
  fresh the first time), and GitHub opens.
- "Make it yours" names the two edits: `repo=` and, in the prompt, which
  desktop the ai-mirror MCP drives.
- The first-run notes say that a folder Claude has never been told to trust
  stops with a message the first time.

### Docs

The README's dev-setup section (`README.md:744-747`) gets the same three
facts, in one short paragraph: it resumes, or starts fresh the first time;
an untrusted folder stops with a message; it focuses herdr.
`docs/index.html` only names the command (`:96`, `:150`), so it is
unchanged.

## Alternatives rejected

- **Reading Claude's transcripts** (the interactive session has
  `"entrypoint":"cli"`, the `-p` one `"sdk-cli"`) to choose `--continue` in
  advance. It saves the one-time 15 s, but it depends on an undocumented file
  format that can change under us.
- **Reading `~/.claude.json` for the trust flag.** It is internal, and it is
  a secret path that Oma's own deny rules refuse (#144).
- **Answering the trust dialog** (sending Down, Enter to the pane). Trusting
  a folder lets Claude read, edit and run code there. That is the user's
  decision, and the intent rules it out.
- **A helper script or a new tool for the chain.** It is one more file and
  one more thing to install, for one example. A subshell does the job inside
  the existing action format.
- **Moving the herdr window to a fixed workspace.** It fights the user's own
  window rules, and a workspace number is personal.
- **Failing on no earlier session with "run claude there once first".** It
  is simpler, but it breaks the example for exactly the user it is written
  for.

## Risks

- **herdr's error codes** (`timeout`, `agent_not_ready`) are herdr's CLI
  contract. A rename falls into the `*)` branch, which prints herdr's output
  and fails, so it degrades to today's behaviour, not worse. The unit test
  pins the codes, and the live check confirms them.
- **A slow resume (over 15 s)** stops with herdr's error, as described above.
  Measured at 3.9 s on p620.
- **`jq` is required,** as it already was.
- **A user's copy made from the old example** keeps the old behaviour, since
  the copy is theirs. The README says to re-copy it. p620's own
  `dev-setup` is not changed.

## Verification

- **Unit test** (`tests/test_actions_cli.py`, next to the examples test at
  `:134`):
  - the example parses and validates;
  - step 2's `command` has no newline;
  - there is no `hypr_dispatch` step;
  - `razer` appears nowhere in the file;
  - with a fake `herdr` on `PATH` (a shell script that answers per
    sub-command from a scenario variable) and the real `jq`, the command is
    run by `sh -c` for each case:
    - `ok`: exit 0, and `agent prompt` got "Continue where we left off."
      and "the other desktop";
    - `timeout`: exit 0, a second `agent start` without `--continue`, and
      the prompt says "new session";
    - `agent_not_ready`: exit 1, the trust sentence on stdout, and no
      `agent prompt`;
    - an unknown error: exit 1, and herdr's output printed;
  - a check that the shell running it survives: the next command in the
    same `sh -c` still runs.
- **The whole suite,** `nix flake check`, and
  `nix build .#omarchy-voice`.
- **Live on p620** (announced on the bus), from a copy of the new example
  aimed at a scratch repo, run by voice through the daemon:
  1. a new scratch folder that isn't trusted → Oma reports the trust
     sentence, and the pane shows the dialog;
  2. trust it by hand, with no earlier session → "new session", and Claude
     answers the prompt;
  3. run again → it resumes ("Continue where we left off");
  4. clean up: the scratch herdr workspaces, the test action and the scratch
     transcripts.
