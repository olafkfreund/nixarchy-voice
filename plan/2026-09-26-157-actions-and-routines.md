---
status: approved
issue: 157
spec: spec/2026-09-26-157-actions-and-routines.md
---

# Plan: Actions and routines

## Approved decisions (carried from the spec, complete)

1. **Format: TOML**, one file per action at
   `~/.config/omarchy-voice/actions/<name>.toml`. Names match `[a-z0-9-]+`, and
   the name is the file name. Reading uses `tomllib`. Writing uses a small
   renderer for this one shape; no new dependency.
2. **Step kinds.** Each step has exactly one of:
   - `tool` + `args`: one of Oma's tools, run through the gate;
   - `ask`: plain words, done by a model;
   - `action`: another action by name.

   Nesting is at most 3 levels, and a cycle is refused at load time. Commands
   run through `run_in_terminal`. `run_shell` is valid only when
   `allow_shell` is on. There is no path around `Executor`.
3. **Routines** are an optional `[schedule]` with `when` (a systemd OnCalendar
   string, `"login"`, or `"every <N><s|m|h|d>"`) and `enabled`. Each enabled
   routine gets a systemd user timer. Event triggers are out of scope.
4. **No agent-watcher.** The herdr example passes its testing instruction to
   Claude in the pane, which already has the ai-mirror and Chrome MCP servers.
   A watcher becomes a follow-up issue only if that proves insufficient.
5. **Menu:** static rows are written between
   `// >>> omarchy-voice actions (generated)` and `// <<< omarchy-voice actions`
   in `~/.config/omarchy/extensions/omarchy-menu.jsonc`. Omarchy 4.0.4 does not
   run user-defined providers (`Menu.qml:338-339`). The file is watched live
   (`Menu.qml:931-936`). Only the block between the markers is rewritten, and a
   `.bak-omarchy-voice` copy of the previous file is kept. A symlinked file is
   left untouched and reported by `doctor`.
6. **Home Manager:** `programs.omarchy-voice.actions.<name>` renders a
   read-only file plus timer and service units of the same names as the
   runtime ones. Runtime `save` refuses those names.
7. **Approvals** live in `~/.local/state/omarchy-voice/approvals.json` as
   `{action: [sha256(gate description)]}`, never in the action file.
   - Deny is always checked first and cannot be approved.
   - An edited step gets a new hash, so its approval lapses.
   - Approval comes only from a human: `action approve` at a terminal, the
     spoken confirm of a `save` (it approves the held steps the readback
     listed), or the spoken confirm of a step held during an interactive run.
8. **`ask` steps.**
   - **During a model turn** (voice or MCP), the runner stops and returns
     *"now do: <text>; then call action run <name> from=<n+1>"*.
   - **Outside a turn** (CLI, menu or timer), a fresh brain runs
     `planner.think(text)`.
   - **Unattended**, a hold inside an ask is cancelled and reported, never run.
9. **The `action` tool:** `{do: list|run|show|save|delete, name?, from?, steps?,
   description?, phrases?, schedule?}`, added to `TOOL_SCHEMAS`, so the local
   planner, the Claude backend and MCP clients all get it.
   - `list` and `show` are read-only.
   - `save` and `delete` always hold, and the hold text reads the recipe back.
   - The schema description names up to 20 existing actions. If the schema plus
     names exceeds ~300 tokens, the names are dropped.
10. **CLI:** `omarchy-voice action list|run|show|new|edit|approve|delete|enable|disable`.
    - `run --unattended` is used by timers.
    - `new` and `edit` go through `omarchy-launch-editor` from a commented
      template, and validate when the editor closes.
    - `save` refuses to rewrite a file that has comments unless given `--force`.
11. **Results.** A routine always sends a notification. When the daemon is up
    it also speaks, through a new control verb, `announce <text>`, which speaks
    without starting a turn.
12. **Examples** go in `share/actions/`: `dev-setup`, `check-email` and
    `morning-repo` (disabled). They are installed only via
    `action new <name> --from <example>`.

## Steps

Each step is one commit on `feat/157-actions-and-routines`, with tests in the
same commit.

1. **`src/omarchy_voice/actions.py` — model and files.**
   - `Action`/`Step` dataclasses.
   - `load(name)` and `load_all()` → `(actions, broken: dict[name, error])`.
   - Validation: the name, exactly one step kind per step, the tool exists in
     `TOOL_SCHEMAS`, `run_shell` requires `allow_shell`, `action` references
     exist, depth ≤ 3, no cycles, `when` parses.
   *Deviation (step 1):* tool arguments are checked against the tool's JSON
   schema at load (required keys, no unknown keys), not its `_validate_*`:
   some validators read the live desktop, and they run anyway inside the gate
   when the step runs.
   - `render_toml(action)`, `save(action, force=False)`,
     `delete(name)` → `actions/.trash/`.
   - `save` refuses a symlink, a file not owned by the user, or a file with
     comments unless `force`.
   - Paths come from `config.CONFIG_HOME` and `config.STATE_HOME`, so
     `tests/_isolated.py` redirects them.

   → Verify by `pytest tests/test_actions.py -q`, covering: a TOML round trip;
   each validation error naming its step number; a cycle; depth 4 refused;
   comment and symlink refusals.

2. **Gate hook — `tools.py`.** `_call_locked(name, args, approved=frozenset())`.
   In the `except NeedsConfirmation` branch, if
   `sha256(description) in approved`, fall through and run the call. The
   branch lies after every `policy.check(read=True)`, so deny is untouched.
   Existing callers pass nothing.

   → Verify by `test_actions.py`:
   - a denied step is still denied when its hash is approved;
   - an approved held step runs;
   - an unapproved one holds;
   - `pytest tests/test_policy.py tests/test_shell_off.py -q` is unchanged
     and green.

3. **Runner and approvals — `actions.py`.**
   - `approvals.load/grant/revoke`, stored as 0600 JSON.
   - `run(name, executor, *, ask, start=1, unattended=False) -> RunResult`,
     executing steps in order:
     - a `tool` step goes to `executor._call_locked(..., approved=…)`;
     - an `action` step recurses;
     - an `ask` step calls `ask(text, n)`.
   - The run stops at the first failure or hold and reports
     "stopped at step n of m: …".
   - `held_steps(action, executor)` dry-checks each step's description against
     the policy, without running it, for `approve` and the `save` readback.

   → Verify by tests for: an unattended run stopping at an unapproved hold;
   an in-turn `ask` returning a continuation with `from=n+1`; a nested action;
   an edited step's approval lapsing.

4. **The `action` tool — `tools.py`.** The schema is added to `TOOL_SCHEMAS`;
   `list`/`show` go in `READ_ONLY_TOOLS`. `_tool_action` dispatches on `do`:
   - `run` passes `ask` as the "yield to the calling model" callback, and on a
     hold sets `self.pending = ("action", {**args, "from": n, "_approve": hash})`;
   - `_approve` is honoured **only** inside `run_pending` (`self._releasing` is
     True), so the model cannot pass it itself;
   - `save`/`delete` are forced to hold in `_call_locked`, as `why` is for
     commands; `describe("action", …)` renders the readback, listing the steps
     that hold.

   The schema description is built from `load_all()` names, capped at 20.
   *Deviations (step 4):* the resume argument is `start`, not `from` (a Python
   keyword, and handlers take arguments as keywords). Under `--dry-run`,
   `action run` reaches its handler and walks the steps, each tool step being
   dry-run on its own, instead of narrating the whole action as one line.

   → Verify by tests:
   - `save` holds and `run_pending` writes the file plus the approvals;
   - a model-supplied `_approve` is rejected outside `run_pending`;
   - `pytest tests/test_mcp.py tests/test_planner.py tests/test_claude_backend.py -q`
     is green, and the MCP tool list contains `action`.

5. **Menu rows — `actions.py`.** `write_menu_rows()`:
   - reads the JSONC, replaces or appends the marker block, and writes via a
     temp file and rename, after copying the previous file to
     `.bak-omarchy-voice`;
   - a symlink is skipped, returning a reason;
   - rows: `voice`, `voice.actions`, `voice.actions.<n>` (a submenu),
     `.run`/`.edit`/`.approve`/`.delete`, `.routine` (with
     `checked: "systemctl --user is-enabled -q omarchy-voice-routine-<n>.timer"`,
     toggling `enable`/`disable`), `voice.new`, `voice.ask` and `voice.folder`.

   → Verify by tests: every byte outside the markers is unchanged across a
   rewrite of a copy of the real file; a symlink is untouched; the generated
   block parses as JSONC. Live: a row appears in the menu without a restart.

6. **Timers — `actions.py`.** `write_timers(launcher)`:
   - generates `omarchy-voice-routine-<n>.service`, with
     `ExecStart=<launcher> action run <n> --unattended` and
     `PartOf=graphical-session.target`;
   - generates `.timer`, with `OnCalendar=`, or `OnBootSec=2m` plus
     `OnUnitActiveSec=` for `every`;
   - `login` is a service with `WantedBy=graphical-session.target` and no timer;
   - it then runs `daemon-reload`, `enable --now` or `disable --now`, and
     removes the files for gone or disabled routines;
   - HM symlinks are skipped;
   - `launcher` is the new config key `routines.launcher`, defaulting to the
     absolute path of `omarchy-voice`.

   → Verify by unit-text tests for `Mon..Fri 08:00`, `every 2h` and `login`, and
   by `systemd-analyze calendar "Mon..Fri 08:00"` in the test when it is
   available.

7. **CLI — `cli.py`.** Add the `action` subparser and `cmd_action`.
   - `run` builds an `Executor` and, for `ask`, a fresh brain via
     `choose_backend` (as `cmd_say` does). Outside a TTY, or with
     `--unattended`, holds are cancelled and reported.
   - It always notifies via `Feedback.notify`, and sends
     `announce <summary>` with `send_control` when `daemon_running()`.
   - `approve` walks `held_steps` asking y/N; `new`/`edit` use
     `omarchy-launch-editor` and then validate; `enable`/`disable` flip
     `schedule.enabled` with `force`, since they must work on commented files.
   *Deviations (step 7):* `enable`/`disable` change `enabled = …` in the
   file's text rather than saving with `force`, which would have dropped the
   user's comments. `new`/`edit` open the editor with `--inline` and the menu
   runs them in the floating terminal, so a terminal editor blocks and the file
   is checked on close; a GUI editor returns at once and is checked at the next
   `action` command. Examples ship inside the package
   (`omarchy_voice/examples/`, package data) rather than `share/actions/`, so
   `--from` needs no path lookup (affects step 10).
   - Every mutating command ends with `write_menu_rows()` and `write_timers()`.
   - `doctor` gains an actions line reporting broken files, a symlinked menu
     file and orphaned timers.

   → Verify by a `tests/test_actions_cli.py` subprocess test in an isolated HOME
   (`list`, `new --from dev-setup`, `show`, `delete`) and by a manual
   `omarchy-voice action run dev-setup`.

8. **`announce` verb — `local_engine.py:876`.** `elif verb == "announce": speak
   the text` through the existing speech path, with no turn and no mic. The mic
   gating from #114 applies because it goes through the same speak call.

   → Verify by `pytest tests/test_local_engine.py -q` plus one new test that
   `announce` speaks and does not call the brain.

9. **Home Manager — `nix/hm-module.nix`.**
   - Extract the key-reading wrapper from `ExecStart` into `runWrapper`, taking
     `"$@"`. The daemon uses `${runWrapper} run`, with the same behaviour.
   - Set `settings.routines.launcher = runWrapper` by default.
   *Deviation (step 9):* not set through `settings`: that would make the module
   write `config.toml` for every user, over a hand-written one when `settings`
   is empty. The wrapper is installed as `omarchy-voice-keyed` on PATH instead,
   and `default_launcher()` prefers it; `routines.launcher` stays as a manual
   override. The wrapper reads `environmentFile` as KEY=value lines without
   evaluating them (sourcing it would run what a value contains). Checked by
   the `hm-actions` flake check, which also runs `bash -n` on the wrapper.
   - Add the option `actions = attrsOf (submodule { description, phrases,
     steps (listOf attrs), schedule { when, enabled } })`, rendering to
     `xdg.configFile."omarchy-voice/actions/<n>.toml"` (via `tomlFormat`) and
     `systemd.user.{services,timers}."omarchy-voice-routine-<n>"`.

   → Verify by `nix flake check`, and an HM eval with one declared routine
   showing the file, the timer and the service in the build output.

10. **Examples and docs.**
    - `share/actions/{dev-setup,check-email,morning-repo}.toml`, installed by
      `package.nix` into `share/omarchy-voice/actions`.
    - `dev-setup`'s herdr step is a single `run_in_terminal` command:
      `p=$(herdr workspace create --cwd <repo> --label dev --focus | jq -r
      '<root pane id path>') && herdr agent start dev --kind claude --pane "$p"
      -- --continue && herdr agent prompt dev "<continue + test on razer via
      ai-mirror / browser via Chrome MCP>"`.
      The jq path is read from a real `workspace create` output during this
      step, and recorded here.
    - A README section "Actions and routines", and one entry in
      `share/config.example.toml` (`[routines] launcher`).

    → Verify by `omarchy-voice action new dev --from dev-setup` then `show`;
    the live dev-setup run opens the workspace, herdr+claude and the browser.

11. **Measure the prompt cost** (#111). Run `omarchy-voice manifest` and
    `tools/bench_local.py` before and after. If the schema plus names
    exceeds ~300 tokens, drop the names (decision 9). Record the numbers here.

## Tests

- `pytest tests -q`: the whole suite stays green (it was green at `647f9e5`).
- `python3 -m unittest discover -s tests` works too, since `_isolated.py`
  supports both.
- `nix build .#omarchy-voice` and `nix flake check`.
- Live on p620, in order:
  1. `action run dev-setup` from the CLI;
  2. the menu row;
  3. "Oma, run dev setup";
  4. "Make that an action called test" → readback → confirm → the file exists
     and the approvals are recorded;
  5. `morning-repo` enabled with `when` a minute ahead → it fires, notifies
     and speaks;
  6. the herdr example end to end, with Claude in the pane reaching razer
     through ai-mirror when it asks for a test.

## Rollback

- Code: revert the merge commit. `action` is new surface only; the one change
  to an existing path is the `approved` parameter of `_call_locked`, which
  defaults to empty, so revert is clean.
- User state: `omarchy-voice action disable` on each routine (or
  `systemctl --user disable --now 'omarchy-voice-routine-*'` and delete those
  unit files).
- Delete the marker block from `omarchy-menu.jsonc`, or restore
  `.bak-omarchy-voice`.
- `~/.config/omarchy-voice/actions/` and `approvals.json` are inert without the
  code and can stay or go.
- HM: remove `programs.omarchy-voice.actions`; the wrapper refactor keeps the
  daemon's `ExecStart` behaviour, so nothing else changes.
