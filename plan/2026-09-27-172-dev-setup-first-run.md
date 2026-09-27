---
status: draft
issue: 172
spec: spec/2026-09-27-172-dev-setup-first-run.md
---

# Plan: dev-setup works the first time a new user runs it

## Approved decisions (from the spec, complete)

1. **Only the shipped example and the README change.** No Python changes.
   p620's own `~/.config/omarchy-voice/actions/dev-setup.toml` is not
   touched.
2. **Steps of `src/omarchy_voice/examples/dev-setup.toml`:**
   - (1) `omarchy_cli launch terminal-herdr`;
   - (2) `run_in_terminal`, with the command below;
   - (3) `open_page https://github.com/notifications` with `read = false`.

   The old `hypr_dispatch focus workspace 4` step is removed.
3. **The `run_in_terminal` command** is a TOML multi-line basic string
   (`"""\` … `"""`) with line-ending `\`, so it parses to **one line**.
   `_validate_run_in_terminal` refuses newlines (`tools.py:4425`). Inside
   a basic string, `\n` for printf is written `\\n`. The command:

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

   - **The subshell,** so `exit 1` never closes the shell in Oma's tmux
     session. The exit marker (#159) reads its status.
   - **15 s for `--continue`,** against a measured 3.9 s resume on p620.
   - **A resume slower than 15 s** stops with herdr's error. That ceiling
     is marked in a `ponytail:` comment, and the upgrade path is a longer
     timeout.
   - POSIX `sh` syntax only.
4. **The cases come from herdr's error code**, measured on p620:
   - `timeout` means nothing to continue (Claude exited, and the pane is back
     at its prompt);
   - `agent_not_ready` means the trust dialog.

   Nothing reads Claude's transcripts or `~/.claude.json`, and nothing
   answers the trust dialog.
5. **Comments in the file:**
   - what happens: herdr is focused or opened, a workspace is created in
     your repository, and Claude resumes there, or starts fresh the first
     time;
   - "Make it yours": `repo=`, and which desktop the ai-mirror MCP drives;
   - a folder never trusted stops with a message the first time;
   - the approval note stays.
6. **README** (`README.md:744-747`): the same facts in one short paragraph,
   plus a line telling anyone who copied the example earlier to copy it
   again. `docs/index.html` is unchanged.

### De-risked before planning (2026-09-27, scratch area)

The command above was written into a TOML file, parsed with `tomllib` (no
newline), and run through `bash -c` and `zsh -c` against a stand-in `herdr`
for each case. Every case matched the spec:

| Case | Result |
| --- | --- |
| `ok` | one `agent start`; the prompt begins "Continue where we left off." |
| `timeout` | two starts (the second without `--continue`); the prompt says "new session" |
| `agent_not_ready` | the trust sentence; no prompt; rc 1 |
| unknown error | herdr's JSON printed; rc 1 |

A command after it in the same shell still ran every time. dash is not
installed here, and `sh` is bash.

## Steps

1. **`src/omarchy_voice/examples/dev-setup.toml`:** rewrite it per decisions
   2, 3 and 5.

   **`tests/test_actions_cli.py`,** new class `TestDevSetupExample` next to
   the examples test (`:134`):
   - it parses, validates (`act.parse` + `act.check`) and has exactly the
     three tools in order;
   - step 2's command has no `\n`;
   - `razer` is not in the file text;
   - **run cases:** a stand-in `herdr` shell script is written to a temp dir
     at test time. It answers from `$SCENARIO`, logs its argv to
     `$HERDR_LOG`, and goes first on `PATH`, with the real `jq`.
     `subprocess.run(["sh", "-c", command + '; echo "alive=$?"'])` for
     `ok`, `timeout`, `agent_not_ready` and an unknown code asserts:
     - the exit status of the block;
     - the number of `agent start` calls;
     - the prompt text (or no prompt);
     - the trust sentence;
     - `alive=` printed, so the shell survived;
   - the test is skipped when `jq` is not on `PATH` (the dev shell has it).

   → verify: `nix develop -c python -m pytest -q tests/test_actions_cli.py`.
   A mutation check (restored from a `cp` backup, never `git checkout`):
   drop the `( … )` and the survival assertion must fail. Commit
   `fix(examples): dev-setup works on a new user's first run (#172)`.

2. **`README.md`:** decision 6. → verify: no other README line contradicts
   it (`grep -n 'workspace 4\|empty.*workspace\|razer' README.md`, checked by
   hand). Commit `docs(readme): what dev-setup does on a first run (#172)`.

3. **The whole suite and the builds.** → verify: `nix develop -c python -m
   pytest -q` (all pass), `nix flake check`, and `nix build .#omarchy-voice`.
   `omarchy-voice action new t --from dev-setup` from that build creates a
   valid copy.

4. **Live on p620** (announced on the bus), run by voice through the
   daemon. Use a copy of the **new** example with `repo=` set to a fresh
   scratch folder: a new git repo that has never been opened in Claude,
   under the scratchpad.
   1. **Untrusted:** Oma reports the step failed with the trust sentence,
      and the herdr pane shows Claude's trust dialog.
   2. **Trust it by hand** in that pane (for the scratch folder only), close
      that herdr workspace, then run again. With no earlier session, Claude
      starts fresh, and the prompt it received says "new session".
   3. **Run again:** it resumes, and the prompt begins "Continue where we
      left off".
   4. **Clean up:** the scratch herdr workspaces, the test action (`action
      delete`, then its trash file), the scratch folder, and its
      `~/.claude/projects/<scratch>` transcripts. Release p620 on the bus.

5. **PR** "fix(examples): dev-setup works on a new user's first run (#172)",
   linking the intent, spec and plan, with the live results.

## Tests

```
nix develop -c python -m pytest -q tests/test_actions_cli.py
nix develop -c python -m pytest -q
nix flake check && nix build .#omarchy-voice
```

## Rollback

Revert the two commits. The example is package data that is copied on
`action new`, so users' existing copies are unaffected either way.
