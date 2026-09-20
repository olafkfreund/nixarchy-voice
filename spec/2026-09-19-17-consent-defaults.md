---
status: approved
issue: 17
intent: intent/2026-09-19-17-consent-defaults.md
---

# Spec: voice never drives the desktop or logs notifications unless the user turns that on

## Design

### `desktop_control`, off by default

- **Config:** a new key under `[hands]`, beside `allow_shell`:
  ```toml
  [hands]
  desktop_control = false   # link ai-mirror: real mouse, keyboard and screen
  ```
  It lives in `Config` (`src/omarchy_voice/config.py`, the `# --- hands` block)
  as `desktop_control: bool = False`, and loads the same way `allow_shell` does.
- **The one place it matters:** `ClaudeBrain._options()`
  (`src/omarchy_voice/claude_backend.py:427-429`). Registering the MCP server
  and appending `AI_MIRROR_PROMPT` both happen only when **both** hold:
  ```python
  if self.config.desktop_control and (ai_mirror := ai_mirror_path()):
  ```
  With it off, the backend never looks for ai-mirror: no server entry, no
  prompt text. `WarmBrain` and `LocalBrain` build on `_options()` through
  `super()`, so one condition covers every brain.
- **Home Manager:** `programs.omarchy-voice.desktopControl`, a
  `lib.mkEnableOption` that defaults to false. It writes
  `settings.hands.desktop_control` through the module's existing
  `settings → config.toml` path (`nix/hm-module.nix:158-160`). The option's
  description says what it hands over: real mouse, keyboard and screen, and
  that ai-mirror still asks a human before control (ai-mirror#10).
- **The prompt text:** `AI_MIRROR_PROMPT` stops saying "Call control with
  mode=agent first". After ai-mirror#10 the call returns `pending` until a
  human answers, so it says: "Ask for control with mode=agent, then wait for
  `status` to show owner=agent; the user is asked to confirm. If they deny or
  it times out, say so and don't ask again this turn."

### `allow_notifications`, off by default

- `Config.allow_notifications: bool = False` (`config.py:368`, currently
  `True`). The comment above it keeps its reasoning (the words versus a
  screenshot trade-off), and adds that the default is now off because it
  records message previews to disk. Turning it on is the user's call.
- The three readers (`realtime.py:1327`, `local_engine.py:506`,
  `tools.py:1026`) already branch on the flag. They need no change.

### The one-time notice (owner's decision)

- `load()` already reads the raw TOML (`config.py:413-419`). It records whether
  `allow_notifications` was **explicitly set**, as
  `Config.allow_notifications_explicit: bool`. That field is not user-facing.
- When the daemon starts, if it was *not* explicitly set **and**
  `STATE_DIR/notifications-off-noticed` doesn't exist, voice does three things:
  1. writes one line to the session log (`LOG_FILE`): "notification logging is
     off by default since 0.4; set `[hands] allow_notifications = true` to turn
     it back on";
  2. sends one desktop notification with the same text;
  3. creates the marker.

  The marker makes it once per install, not once per start.
- **An existing user who set the key** keeps it and sees no notice (intent
  constraint).

### `doctor` reports both

Under the existing `hands` lines (`cli.py:233-260`, next to `shell tool:`):

```
  desktop control: disabled   (set [hands] desktop_control = true, or programs.omarchy-voice.desktopControl)
  notification log: disabled  (records notification bodies when on)
```

When enabled, desktop control also shows whether ai-mirror was found:
`enabled, ai-mirror at /nix/store/…` or `enabled, but ai-mirror is not
installed`.

## Alternatives rejected

- **Gating on an environment variable only.** `OMARCHY_VOICE_AI_MIRROR`
  already exists, but only to point at a binary. Reusing it for consent would
  make "installed" and "allowed" the same thing again, which is the bug.
- **Asking at runtime** ("may I use ai-mirror?") every turn. It's noisy, and
  ai-mirror#10 already asks the human before control is granted. Consent to
  *link* the tool at all is a configuration decision, made once.
- **A startup notice on every start.** The owner decided on once. The marker
  in `STATE_DIR` is the smallest way to remember it.

## Risks

- **Users who relied on "installed means linked" lose desktop control** until
  they opt in. That's the point. The release notes and `doctor` say how to turn
  it back on.
- **Notification-aware answers stop working by default** ("what was that
  notification"). `read_screen` is still there, but costs a screenshot. That's
  the trade-off the config comment already names.
- **A stale marker after a config reset** means no second notice. That's
  acceptable, because the notice is informational.

## Verification

Tests go in the existing files, each red on `main` (`481d3f3`) first, then
green:

| Test | File | Red today because |
|---|---|---|
| **Runtime path:** ai-mirror "installed" (`AI_MIRROR_ENV` set to a real executable), `desktop_control` unset, so `_options().mcp_servers` has **no** `ai-mirror` key, and the system prompt has no `mcp__ai-mirror__` text | `tests/test_claude_backend.py` (the `options()` helper, `:416-426`) | today installed means linked |
| the same with `desktop_control = true`, so the server **and** the prompt are present | same | there is no key yet |
| `Config()` defaults: `desktop_control is False`, `allow_notifications is False` | `tests/test_config.py` | `allow_notifications` defaults to `True` |
| a TOML without the key gives `allow_notifications_explicit is False`; with it, `True` | `tests/test_config.py` | there is no field |
| notice: first start with the key unset logs once and creates the marker; the second start doesn't log; an explicit key never logs | `tests/test_notifications.py` | there is no notice |

**The existing `test_installed_ai_mirror_is_a_second_server` (`:428`) is
rewritten, not deleted.** It sets `desktop_control = true`, because it
describes the opted-in case. The PR says so. `test_no_ai_mirror_no_server`
(`:434`) is unchanged.

`nix flake check` (the repo's CI, `.github/workflows/check.yml`) runs the
suite through the existing `checks.<system>.unit`. The Home Manager option gets
one new check, `checks.<system>.hm-desktop-control`. It evaluates the module
with `desktopControl = true`, then with it unset, and asserts the generated
`config.toml` has `hands.desktop_control = true` in the first case and no key in
the second. It is shown red first by writing the option to the wrong settings
key. CI already runs `nix flake check`, so adding the check needs no workflow
edit.
