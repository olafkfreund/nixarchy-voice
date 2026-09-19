---
status: draft
issue: 17
author: olafkfreund
---

# Intent: voice never drives the desktop or logs notifications unless the user turns that on

## Problem

Two of voice's defaults act without anyone asking.

**It hands its agent the desktop.** When `ai-mirror` is on `PATH`,
`claude_backend.py:427-429` registers it as an MCP server and appends
`AI_MIRROR_PROMPT` (`:135-139`), which tells the agent to call
`control mode=agent` before it acts. ai-mirror accepts that grant without a
human (olafkfreund/ai-mirror#10). So installing both packages, which nixarchy
may do for ai-mirror by default, is enough for a voice command to move the
mouse and type into whatever window is focused. Nobody opted in to that
anywhere. Having a package installed is not consent.

**It records notifications from the first run.** `allow_notifications`
defaults to `True` (`config.py:368`, `share/config.example.toml:168`), so the
bodies of desktop notifications, message previews included, go into
`~/.local/state/omarchy-voice/notifications.jsonl` before the user has made
any choice.

The wake word is already off by default (`config.py:343`), so it needs nothing.

nixarchy wants voice as an opt-in setup (olafkfreund/nixarchy#774), and will
only offer it once both defaults are off.

## Proposed outcome

- Voice links ai-mirror only when the user sets an explicit option:
  `desktop_control = true` in the config, surfaced as
  `programs.omarchy-voice.desktopControl` (default `false`). With it off,
  ai-mirror being installed changes nothing: no MCP entry and no prompt text.
- `allow_notifications` defaults to `false`. Existing users who set it keep
  their setting.
- `omarchy-voice doctor` reports both settings, so a user can see what voice
  can reach.

## Affected users and systems

Everyone running voice with the Claude backend, and especially machines that
also have ai-mirror. It affects the config defaults, the Claude backend's MCP
assembly, the Home Manager module and doctor. nixarchy's #774 depends on it.

## Constraints

- **The test is the runtime path**, not the Nix config. With both packages
  installed and `desktop_control` off, the MCP server set the backend builds
  must not contain ai-mirror. That check must fail on today's code first.
- No behaviour change for a user who has already set these options.
- Nothing else in the gate (#7, #14) loosens.

## Open questions

1. **Existing users with default config:** turning notification logging off
   changes what they get, silently. Say so in the release notes only, or also
   log it once at startup?
