---
status: draft
issue: 157
author: olafkfreund
---

# Intent: Actions and routines

## Problem

Oma does one thing per sentence and forgets how it did it. Anything the user
does every day has to be said again, step by step, every day:

- "Open my development workspace" is five requests: a new Hyprland workspace,
  herdr attached to the right session, a Claude session in it, a browser on
  GitHub, maybe the mail client on the side.
- "What happened in the repo last night" is a question the user has to
  remember to ask, and phrase the same way each time to get the same answer.
- "Continue development where we left off, and when it asks for testing,
  drive the test on razer or in the browser" is a standing instruction, not
  a one-shot command. Nothing today can hold it.

The only memory that outlives a session is `remember`
(`src/omarchy_voice/tools.py:1459`), a list of free-text notes. It cannot be
run, scheduled, shared or edited from the menu.

There is also no way to see or manage any of this without a terminal: the
Omarchy menu has no voice section at all.

## Proposed outcome

1. **Actions.** A named recipe of one or more steps, saved as a file the user
   can read. Run it three ways, with the same result:
   - by voice — "run dev setup", "check my email";
   - from the Omarchy menu — a *Voice → Actions* submenu lists every action;
   - from the CLI — `omarchy-voice run dev-setup`.
2. **Two kinds of step, mixable in one action.**
   - *Do* steps: a fixed tool call with fixed arguments (switch to workspace 4,
     launch the browser on a URL, `herdr agent start ... --kind claude`). Same
     every time, no model involved, fast.
   - *Ask* steps: an instruction in plain words handed to the model ("summarise
     what merged and what failed in nixarchy-voice since 18:00 yesterday").
     This is what makes "check my email" or "tell me what happened" possible.
3. **Routines.** An action plus *when*: a time of day, an interval, at login,
   or on an event. Routines keep running with the voice daemon off, and say or
   show their result (spoken if listening, a notification if not).
4. **Standing instructions** for long-running work, so the herdr example works:
   start the Claude session, hand it "continue where we left off", and when that
   agent asks for testing, run the named test action (razer desktop, browser).
5. **Create and edit without a text editor.** Say "make that an action called
   dev setup" after doing it by hand, or "create a routine that checks my email
   at 8", and Oma writes the file and reads it back for approval. The menu has
   *New action*, *Edit*, *Run*, *Enable/disable routine*, *Delete*.
6. **Easy for a new user, deep for a developer.** A new user never opens a
   file. A developer can hand-write, version and share the files, or declare
   them in Home Manager.

## Affected users and systems

- The voice daemon: `tools.py`, `persona.py`, `capabilities.py`, `cli.py`,
  `mcp_server.py` (an MCP caller should be able to list and run actions too).
- `nix/hm-module.nix`: declared actions and routines, and the systemd user
  timers that run routines.
- The Omarchy menu, through `~/.config/omarchy/extensions/omarchy-menu.jsonc`
  (rows with `provider` can be generated at runtime, so the list stays live).
- herdr (`herdr workspace / agent start / agent prompt / agent wait`), and
  ai-mirror for the "test on that desktop" part.
- Every user: p620 (this desktop) and razer.

## Constraints

- **The policy gate still applies to every step.** An action is a shortcut for
  saying things, not a way around deny rules or confirm holds. A saved action
  must not be able to do what the same sentence could not.
- **Unattended means nobody can confirm.** A routine running at 03:00 has no
  one to say "confirm". A step that would be held must either be refused in a
  routine or be approved once, explicitly, when the routine is saved — never
  silently allowed.
- **Creating an action is itself a confirmed step.** Oma reads the recipe back
  before saving; a misheard sentence must not save a recipe.
- No secrets in action files. Credentials stay in 1Password / the environment,
  referenced, never written in.
- Human-editable, diffable, commentable format. One file per action so they can
  be shared and version-controlled individually.
- Works with the daemon off (CLI and menu), and on both engines (Claude and
  local).
- No new always-running service if systemd user timers can carry the schedule.

## Open questions

1. **File format.** You suggested JSON. JSON has no comments and is unkind to
   hand-edit; TOML matches `config.toml` and allows comments; YAML is friendlier
   still but is a new dependency and has footguns. **Recommendation: TOML**, one
   file per action in `~/.config/omarchy-voice/actions/`, with a JSON Schema
   published so editors can validate it. OK?
2. **Where "do" steps come from.** Only Oma's own tools (typed, gated, the same
   as voice), or also raw shell commands (only when `allow_shell` is on)?
   **Recommendation:** Oma's tools, plus `run_shell` under the same switch it
   has today.
3. **Scheduler.** systemd user timers generated per routine (survive reboot,
   visible in `systemctl --user list-timers`, no daemon needed) versus a
   scheduler inside the daemon (can react to Hyprland events). **Recommendation:**
   timers for time-based routines; event triggers ("when I plug in the dock")
   deferred to a later issue.
4. **Standing instructions scope.** "When it asks for testing, test on razer"
   means watching a herdr agent and reacting. Is that in this issue, or a
   follow-up once plain actions and routines work? **Recommendation:** follow-up;
   this issue ships an action that starts the session and hands over the prompt,
   and a separate *test on razer* action the agent can be told to call.
5. **Menu surface.** A JSONC `provider` row (fits Omarchy today, no QML) versus
   a Quickshell panel like `nixarchy.pkg` (richer editing). **Recommendation:**
   provider rows first; editing opens the file in the editor or asks Oma.
6. **Home Manager.** Should actions/routines also be declarable in Nix
   (`programs.omarchy-voice.actions.<name>`), read-only alongside the user's own?
