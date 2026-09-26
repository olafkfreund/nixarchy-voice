---
status: draft
issue: 157
intent: intent/2026-09-26-157-actions-and-routines.md
---

# Spec: Actions and routines

## Decisions on the intent's open questions

The intent was approved without answers, so each is decided here with the
reason. Any of them can be overturned at this review.

| # | Question | Decision | Why |
|---|---|---|---|
| 1 | Format | **TOML**, one file per action in `~/.config/omarchy-voice/actions/<name>.toml` | Comments and hand-editing; `tomllib` is stdlib (`config.py:12`) and `config.toml` is already TOML. JSON has no comments. YAML is a new dependency. Writing uses a ~30-line renderer for this one fixed shape, not a new dependency. |
| 2 | "Do" steps | **Oma's own tools only**, through `Executor.call`. Commands go through `run_in_terminal` (visible, gated). `run_shell` works only when `allow_shell` is on, as today (`tools.py:4040`). | One gate, no second path. An action can then do nothing that the same sentence could not. |
| 3 | Scheduler | **systemd user timers**, one per routine. Event triggers are deferred. | They survive a reboot, show in `systemctl --user list-timers`, run with the daemon off, and need no new process. |
| 4 | Standing instructions | **Not built as a watcher.** The herdr example works without it (see *The herdr example*). A watcher that reacts to agent state becomes a follow-up issue, filed only if the example below falls short. | Claude Code in the herdr pane already has the ai-mirror and Chrome MCP servers. Telling *it* how to test is enough; Oma does not need to watch it. |
| 5 | Menu | **Static rows written into the user's menu file**, between marker comments. | Revised after research: Omarchy 4.0.4 does **not** run user-defined providers. `startProviderForMenu` looks the name up in a hard-coded table (`fonts`, `power-profiles`, `apps`) and returns if it is not there (`shell/plugins/menu/Menu.qml:338-339`), whatever the JSONC comment says. The user file *is* watched live (`Menu.qml:931-936`), so rewritten rows appear without a restart. |
| 6 | Home Manager | **Yes**: `programs.omarchy-voice.actions.<name>` declares an action (and its routine timer) as a read-only file. | Keeps a Nix user's recipes versioned with the rest of their config; costs one option and one `mapAttrs`. |

## Design

### The file

```toml
# ~/.config/omarchy-voice/actions/dev-setup.toml
description = "Open my development workspace"
phrases = ["dev setup", "development workspace"]   # optional: what you might say

[[step]]                        # a "do" step: one of Oma's tools, fixed arguments
tool = "hypr_dispatch"
args = { dispatcher = "focus", args = { workspace = "4" } }

[[step]]
tool = "run_in_terminal"
args = { command = "herdr" }

[[step]]
tool = "launch_app"
args = { app = "chromium", url = "https://github.com/olafkfreund" }

[[step]]                        # an "ask" step: plain words, done by the model
ask = "Tell me what merged and what failed in nixarchy-voice since 18:00 yesterday."

[[step]]                        # call another action: groups of groups
action = "check-email"

[schedule]                      # present = this action is also a routine
when = "Mon..Fri 08:00"         # systemd OnCalendar, or "login", or "every 2h"
enabled = true
```

Each step has exactly one of `tool` (with `args`), `ask` or `action`. An
`action` step nests at most 3 levels deep, and a cycle is refused when the file
is loaded, not when it runs. Names are `[a-z0-9-]+`, and the name is the file
name.

### New module: `src/omarchy_voice/actions.py`

- `load(name)` / `load_all()`: parse and validate. Unknown tool names, bad
  arguments (each tool's own `_validate_<tool>`), missing fields, cycles, and
  a `run_shell` step while `allow_shell` is off are all errors that name the
  step. A file with an error is listed as broken and never run.
- `save(action)` / `delete(name)`: write the TOML, or move the file to
  `actions/.trash/`. Refuses to overwrite anything that is not a regular file
  the user owns, which covers a Home Manager symlink into the store.
- `run(name, executor, ask)`: execute the steps in order.
  - A `tool` step calls the gate's single entry point, `Executor._call_locked`
    (`tools.py:2015`). It is called with the lock already held, because the
    runner itself runs inside a tool call and the lock is not re-entrant.
  - An `ask` step is handed to the `ask` callback (below).
  - The run stops at the first failure or unapproved hold and says which step
    it reached ("stopped at step 3 of 5: …").
- **Approvals** live in `~/.local/state/omarchy-voice/approvals.json`, as
  `{action: [sha256 of the step's gate description]}`. `_call_locked` takes an
  `approved: set[str]`. If a step raises `NeedsConfirmation` and its
  description's hash is in the set, the step runs. **Deny is checked first and
  can never be approved.** Editing a step changes its description and hash, so
  the approval lapses. Approvals live outside the action file: a file the model
  wrote must not carry its own approval.
- `menu_rows()` → rewrites the block between
  `// >>> omarchy-voice actions (generated)` and `// <<< omarchy-voice actions`
  in `~/.config/omarchy/extensions/omarchy-menu.jsonc`. Every other line is
  left byte-for-byte as it was. If the file is a symlink (Home Manager owns
  it), it is left untouched and `doctor` reports that.
- `timers()` → for each action with an enabled `[schedule]`, writes
  `~/.config/systemd/user/omarchy-voice-routine-<name>.{service,timer}`, runs
  `daemon-reload` and `enable --now`. A disabled or removed schedule is
  `disable --now`d and its files deleted. A unit that is a symlink (Home
  Manager) is left alone. `login` maps to `WantedBy=graphical-session.target`
  with no timer; `every 2h` maps to `OnUnitActiveSec=2h` plus `OnBootSec`.

`save`, `delete`, `enable` and `disable` all end by calling `menu_rows()` and
`timers()`, so the menu and the timers can never drift from the files.

### Who runs the "ask" steps

| Where the run started | `ask` callback |
|---|---|
| **Voice / MCP** — the model called the `action` tool mid-turn | The runner stops and returns the done steps plus *"now do: \<ask text\>; then call `action run <name> from=<n+1>`"*. The model already in the turn does the work. There is no nested brain, and it works the same on the Claude and local engines. |
| **CLI, menu, timer** — no turn in progress | `planner.think(text)` with a fresh brain, as `cmd_say` does (`cli.py:129`). The reply is collected. |

A held step inside an `ask` with nobody present (a menu or timer run) is
cancelled, logged as `HOLD` → `CANCEL`, and reported. It is never run.

### The `action` tool (voice and MCP)

One schema is added to `TOOL_SCHEMAS`, so the local planner, the Claude
backend and external MCP clients all get it from the same list
(`planner.py:165`, `mcp_server.py:125`):

```
action: {do: list|run|show|save|delete, name?, from?, steps?, description?, phrases?, schedule?}
```

- `list` puts each action's name, description and phrases into the result. The
  schema description names the actions that exist, capped at 20, so "run dev
  setup" needs no `list` round trip first.
- `list` and `show` are in `READ_ONLY_TOOLS`.
- `save` and `delete` **always** hold, whatever the confirm patterns say. The
  hold text reads the whole recipe back, each step as its gate description.
  Confirming a `save` also approves the steps that the readback listed as
  needing confirmation, because the user heard them.
- In an interactive `run`, an unapproved held step parks the `action` call
  itself, with `from=<n>`, as the pending call. "Confirm" approves that step
  permanently for that action and runs the rest. `run_pending` needs no
  change: it re-enters `_tool_action` with the same arguments.
- *"Make what I just did an action called dev setup"*: the model has its own
  tool calls in context and passes them as `steps`. Nothing new is recorded
  for this.

### CLI

`omarchy-voice action list | run <name> | show <name> | new <name> | edit <name> | approve <name> | delete <name> | enable <name> | disable <name>`

(`run` alone is taken: `omarchy-voice run` starts the daemon, `cli.py:207`.)

- `new` / `edit` open the file in `$EDITOR` through `omarchy-launch-editor`,
  starting from a commented template, and validate it on close. An invalid
  file is reported, not saved over.
- `approve` prints each step that would hold and asks `y/N` for each, at a
  terminal only.
- A routine's `.service` runs `omarchy-voice action run <name> --unattended`.
  The result always goes out as a desktop notification. If the daemon is up,
  it also goes out as speech through a new control verb, `announce <text>`
  (`local_engine.py:876`, next to `say`), which speaks without starting a
  turn.

### Menu rows (generated)

```
Voice ▸ Actions ▸ dev-setup ▸ Run | Edit | Approve… | Delete
                  morning-repo ▸ Run | Edit | Approve… | Routine ✓ | Delete
        New action…     (template in the editor)
        Ask Oma to make one  (starts listening: omarchy-voice listen start)
        Open folder
```

`Routine ✓` uses the menu's `checked` field, which checks
`systemctl --user is-enabled`, and toggles `enable`/`disable`. `Approve…` and
`Edit` open a terminal or editor, because they need one.

### Home Manager

```nix
programs.omarchy-voice.actions.morning-repo = {
  description = "What happened in the repo overnight";
  steps = [ { ask = "Summarise ..."; } ];
  schedule.when = "Mon..Fri 08:00";
};
```

This renders to `xdg.configFile."omarchy-voice/actions/<name>.toml"`, a
read-only symlink, and to `systemd.user.timers/services` of the same names as
the runtime ones. Runtime `save` refuses those names, because a symlink is not
a regular file. Declared steps still need `approve`: approval is always given
at run-time by the user, never declared.

### The herdr example

*"Log in to herdr, start a new Claude session and continue where we left off;
when it asks for testing, drive the test on razer or in the browser"* becomes
one action:

1. `hypr_dispatch` focus workspace 4.
2. `run_in_terminal`: `herdr workspace create dev` (or `focus`, if it exists).
3. `run_in_terminal`: `herdr agent start dev --kind claude --pane <pane> -- --continue`.
4. `run_in_terminal`: `herdr agent prompt dev "Continue where we left off.
   When you need a UI test, use the ai-mirror MCP on razer for the desktop
   (ssh razer, see its env notes) or the Chrome MCP for the browser."`
5. `launch_app` the browser on GitHub.

Steps 2–4 hold, because the shell is off. They are approved once, when the
action is saved, and after that it runs hands-free. The exact herdr pane
arguments (`--pane <id>` needs an existing pane) are confirmed against the
real CLI during implementation, and the plan records what they turned out to
be.

### Examples shipped

`share/actions/`: `dev-setup.toml`, `check-email.toml` (an ask step that uses
`gog`/Gmail through the Claude engine), `morning-repo.toml` (a routine,
disabled by default). They are copied in by `action new --from <example>`,
never installed automatically.

## Alternatives rejected

- **JSON** (your first suggestion): no comments, and trailing-comma errors
  when edited by hand. TOML covers the same shapes.
- **An action is only a prompt** ("do these five things"): it is slow, it can
  come out differently each time, and it costs model tokens for what is really
  a fixed sequence. The `ask` step keeps that as an option per step.
- **A scheduler inside the daemon**: dead when the daemon is off, and it would
  duplicate systemd. It would be reconsidered only for event triggers.
- **A menu `provider`**: not supported for user entries in Omarchy 4.0.4 (see
  decision 5).
- **A Quickshell editing panel like `nixarchy.pkg`**: a large QML surface for
  something the editor and voice already cover. Revisit if people ask for it.
- **Approvals kept in the action file**: a recipe the model wrote could then
  approve itself.
- **A nested brain for `ask` steps during a voice turn**: two models in one
  turn, a second confirm gate, and double the latency. The yield-to-caller
  design avoids all three.

## Risks

- **Prompt cost.** One more tool schema plus up to 20 action names is on
  every turn (#111). This is measured with `omarchy-voice manifest` and
  `tools/bench_local.py`. If it grows past ~300 tokens, the names come out of
  the schema and `list` is used instead.
- **Editing the user's menu file.** A bug could corrupt a hand-edited file.
  Mitigations: only the block between the markers is replaced, the previous
  file is kept as `.bak-omarchy-voice`, and a round-trip test asserts every
  byte outside the markers is unchanged.
- **Unattended runs on razer and p620.** A timer fires while the user is away,
  with the real desktop. Only approved steps run; an unapproved hold stops the
  routine. `desktop_control` still asks through ai-mirror (#10).
- **Approval by a misheard "confirm".** The same exposure as any hold today,
  already guarded by #86 and #113. A save's readback is longer, which makes a
  mistaken yes less likely.
- **herdr CLI changes.** The example action is data, not code: if herdr
  changes, the file breaks and the code does not.
- **TOML rewrites drop comments.** `save` rewrites the whole file, so comments
  in a hand-written file edited by voice are lost. `save` refuses to rewrite a
  file containing comments unless given `--force`, and tells the user to edit
  it by hand.

## Verification

- `tests/test_actions.py`:
  - load/validate: a bad tool, bad arguments, a cycle, depth > 3, and
    `run_shell` with the shell off are each refused with the step number.
  - TOML round trip: save then load returns the same data.
  - gate: a denied step is denied even when "approved"; an approved held step
    runs; an edited step's approval lapses; an unapproved hold stops the run
    at that step.
  - in-turn `ask` returns the continue instruction with `from=n+1`.
  - menu block rewrite: bytes outside the markers are unchanged, and a symlink
    is left alone.
  - timer unit text for `08:00`, `every 2h` and `login`.
- `nix build` and `nix flake check`. An HM evaluation with one declared action
  produces the file and the timer.
- Live, on p620:
  - `omarchy-voice action run dev-setup` opens the workspace, herdr and the
    browser.
  - The Voice ▸ Actions row does the same.
  - "Oma, run dev setup" does the same.
  - "Make that an action called test" reads the recipe back and saves it on
    confirm.
  - `morning-repo` enabled with `when` a minute ahead fires, and notifies and
    speaks.
- Live, the herdr example end to end, including Claude in the pane reaching
  razer through ai-mirror when it asks for a test.
