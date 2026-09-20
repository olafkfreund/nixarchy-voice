---
status: approved
issue: 17
spec: spec/2026-09-19-17-consent-defaults.md
---

# Plan: voice does not drive the desktop, or log notifications, until asked

## The decisions being implemented

Carried over from the approved spec so this file stands alone.

**Why.** Two capabilities are on today because something is *installed*
rather than because anyone chose them. If `ai-mirror` is on `PATH`,
`ClaudeBrain._options()` registers it and appends `AI_MIRROR_PROMPT` — real
mouse, keyboard and screen, with no setting anywhere saying yes. And
`allow_notifications` defaults to `True`, so notification bodies (message
previews included) are read and written to the session log by default.
Installing something is not consenting to it.

**Settled decisions — do not relitigate while implementing:**

1. **`desktop_control`, a new `[hands]` key, default `False`**, loaded exactly
   the way `allow_shell` is.
2. **One gate, at the one place the link is made**:
   `claude_backend.py:427-429` becomes
   `if self.config.desktop_control and (ai_mirror := ai_mirror_path()):`.
   Both the server entry and the prompt text hang off it. `WarmBrain` and
   `LocalBrain` reach `_options()` through `super()`, so one condition covers
   every brain — do not add a second check anywhere.
3. **`AI_MIRROR_PROMPT` changes with it.** ai-mirror#10 makes
   `control mode=agent` return `pending` until a human answers, so the old
   "call control with mode=agent first" is now wrong. New wording: ask for
   control, then poll `status` until `owner=agent`; the user is asked to
   confirm; on a deny or a timeout say so and do not ask again this turn.
4. **`allow_notifications` flips to `False`** (`config.py:368`). The three
   readers (`realtime.py:1327`, `local_engine.py:506`, `tools.py:1026`)
   already branch on it and are not touched.
5. **A one-time notice, not a per-start one** (owner's decision). Shown only
   when the key was *not* explicitly set and the marker is absent.
6. **An existing user who set the key keeps it and sees nothing** — that is
   the intent constraint, and it is what `allow_notifications_explicit` is
   for.
7. **`doctor` reports both**, under the existing `hands` lines.
8. **The Home Manager option is `programs.omarchy-voice.desktopControl`**, a
   `mkEnableOption` writing through the module's existing `settings →
   config.toml` path — no new file-writing code.

**Landing order.** #17 and #18 are siblings off `481d3f3`, and both touch
`nix/hm-module.nix` and `cli.py`'s doctor. **#17 lands first**: it is the
smaller change, it is the security-relevant one, and #18's packaging work has
no dependency on it. #18 then rebases onto the new `main`
(`git rebase main`), resolving in `hm-module.nix` (this branch adds a
`desktopControl` option; #18 rewrites the `xdg.configFile` block) and in
`cli.py` (this branch adds two doctor lines; #18 adds the voice-attribution
line). Both are additive in different regions; if the rebase is messy, replay
#18's own commits onto `main` rather than merging `main` into #18.

## Steps

1. `src/omarchy_voice/config.py`: add `desktop_control: bool = False` to the
   `# --- hands` block beside `allow_shell`, with a one-line comment naming
   what it hands over (real mouse, keyboard, screen).
   → verify by `Config().desktop_control is False` and a `config.toml` with
     `[hands] desktop_control = true` loading as `True`

2. `src/omarchy_voice/config.py:368`: `allow_notifications: bool = True` →
   `False`. Keep the existing comment's reasoning (words versus a screenshot)
   and add why the default moved: it records message previews to disk.
   → verify by `Config().allow_notifications is False`

3. `src/omarchy_voice/config.py:413-419`: `load()` already has the raw TOML —
   record `allow_notifications_explicit: bool` on the `Config` it returns.
   Not user-facing; do not document it as a key.
   → verify by loading a TOML with and without the key

4. `src/omarchy_voice/claude_backend.py:427-429`: the gate becomes
   `if self.config.desktop_control and (ai_mirror := ai_mirror_path()):`.
   Nothing else in the method moves.
   → verify by `_options()` having no `ai-mirror` key when the flag is off,
     with the binary present

5. `src/omarchy_voice/claude_backend.py`: rewrite `AI_MIRROR_PROMPT` per
   decision 3 — ask, poll `status`, accept a deny.
   → verify by the string containing no "call control with mode=agent first"
     and the opted-in test asserting the new text reaches the system prompt

6. `src/omarchy_voice/cli.py`: a `consent_notice(config)` helper — when
   `not config.allow_notifications_explicit` and
   `STATE_DIR/notifications-off-noticed` is absent, write one line to
   `LOG_FILE`, send one desktop notification with the same text, then create
   the marker. Call it from `cmd_run` (`:176`) before the engine starts, so
   both engines get it from one call site. Reuse `feedback.py`'s
   `notify-send` path rather than adding a second spawner; a missing
   `notify-send` must still log and still write the marker.
   → verify by a first run logging once and creating the marker, and a second
     logging nothing

7. `src/omarchy_voice/cli.py`, beside `shell_status`: a `consent_status`
   helper returning the two lines the spec fixes —
   `desktop control: disabled (set [hands] desktop_control = true, or
   programs.omarchy-voice.desktopControl)` and `notification log: disabled
   (records notification bodies when on)`. When desktop control is enabled,
   the line also says `ai-mirror at <path>` or `but ai-mirror is not
   installed`. Call it from `doctor` under the `hands` lines. A helper, not
   four prints, for the same reason `shell_status` is one: the answer is
   conditional and worth a test.
   → verify by `omarchy-voice doctor` showing both lines in each state

8. `nix/hm-module.nix`: `desktopControl = lib.mkEnableOption "…"` (default
   false), written into `settings.hands.desktop_control` through the existing
   `settings` merge at `:158-160`. The description says it hands over real
   mouse, keyboard and screen, and that ai-mirror still asks a human before
   control (ai-mirror#10).
   → verify by evaluating the module both ways and reading the generated
     `config.toml`

9. `flake.nix`: `checks.<system>.hm-desktop-control` — evaluate the Home
   Manager module with `desktopControl = true` and with it unset, assert
   `hands.desktop_control = true` in the generated `config.toml` for the first
   and **no such key** for the second. CI already runs `nix flake check`, so
   no workflow edit (and none is ours to make).
   → verify by `nix build .#checks.x86_64-linux.hm-desktop-control`

10. `README.md`, `docs/`, and the release notes: both defaults changed, how to
    turn each back on, and that the notice appears once. This is a behaviour
    change for existing users — the note is not optional.
    → verify by reading the diff

## Tests

The repo's runner is `pytest tests -q` (`nix develop`, or
`checks.<system>.unit`). Each case below must be **seen red on `481d3f3`**
first, and the failing output goes in the PR (§1).

| Case | File | Red today because |
|---|---|---|
| `AI_MIRROR_ENV` points at a real executable, `desktop_control` unset → `_options().mcp_servers` has no `ai-mirror` key **and** the system prompt has no `mcp__ai-mirror__` text | `tests/test_claude_backend.py` (`options()` helper, `:416-426`) | installed means linked today |
| the same with `desktop_control = true` → server **and** prompt present | same | the key does not exist |
| `Config().desktop_control is False`, `Config().allow_notifications is False` | `tests/test_config.py` | `allow_notifications` is `True` |
| TOML without the key → `allow_notifications_explicit is False`; with it → `True` | `tests/test_config.py` | the field does not exist |
| first start, key unset → one log line and the marker; second start → nothing; key set explicitly → never | `tests/test_notifications.py` | there is no notice |
| doctor prints both lines, and the enabled form names ai-mirror's path or its absence | `tests/test_backend_choice.py`, beside the `shell_status` tests at `:332-343` | the lines do not exist |

**`test_installed_ai_mirror_is_a_second_server` (`:428`) is rewritten, not
deleted** — it becomes the opted-in case with `desktop_control = true`,
because that is the behaviour it always described. Say so in the PR (§1's
other half: a check that goes red on a deliberate change may be measuring the
arrangement, and this one is). `test_no_ai_mirror_no_server` (`:434`) is
unchanged and must stay green untouched.

The `hm-desktop-control` check is shown red by writing the option to the wrong
settings key — not by deleting the option, which would fail for the wrong
reason.

```sh
pytest tests -q
nix build .#checks.x86_64-linux.unit .#checks.x86_64-linux.hm-desktop-control
```

## Rollback

`git revert` the commits. Reverting restores the old defaults, and a user who
opted in keeps a `desktop_control` key that the old code ignores — harmless.
The only residue is `STATE_DIR/notifications-off-noticed`, an empty marker in
`~/.local/state/omarchy-voice/`; deleting it is the whole cleanup, and leaving
it costs nothing.

**Dependants:** #18 rebases onto this (see landing order). ai-mirror#10 is the
other half of the same consent story — this decides whether the link exists at
all, that decides who may use it once it does.
