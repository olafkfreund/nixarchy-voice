---
status: draft
issue: 69
author: olafkfreund
---

# Intent: the voice engine must see the desktop as it is now, not as it was at start

Closes #69.

## Problem

On the local engine, which is the default, the model is shown a picture of the
desktop taken when the daemon started, and is told that picture is current.

`ClaudeBrain._options()` builds the system prompt once
(`src/omarchy_voice/claude_backend.py:436` → `planner._system_prompt()`,
`planner.py:109-114`), and that prompt embeds `capabilities.live_state()`:
monitors, workspaces, the focused window, and every open window with its
address. `WarmBrain` then holds that one session for its whole life, and
`_turn()` (`claude_backend.py:723-726`) sends only what the user said. Nothing
in `LocalSession._answer` (`local_engine.py:339-404`) refreshes it. The
daemon on this machine has been up for 1 day 10 hours, so the "desktop right
now" it hands the model is 34 hours old.

The persona makes this worse, not better. It tells the model the snapshot "is
read off the live system at the start of every turn", and to not "spend a
hypr_query re-fetching what it already told you" (`persona.py:48-59`).
`live_state()`'s own docstring says "refreshed on every request". So the model
trusts it, does not look, and acts on window addresses that closed hours ago.

This is the cause of the repair turns in `session.log`:

```
'Check the terminal. Check the terminal now. You can see the path.'
"It didn't start anything."
"I don't see in the cloud session in the terminal. Where is it?"
'Check again.'
```

Each of those is a whole extra turn (1.5-7.6 s, intent #23) that the user
paid for because the first answer was built on the old desktop. The user asked
for commands they do not have to explain; this is the most basic reason they
currently have to.

The other engines are not affected. The realtime engine already appends a
fresh snapshot per turn and deletes the old one (`realtime.py:645-701`), and
the typed `say` path builds a new `ClaudeBrain`, and so a new snapshot, on
every call (`cli.py:97-105`). Only the long-lived `WarmBrain` goes stale.

Found in the 2026-09-24 review (Fable 5.1 code deep-dive; confirmed by reading
the code, and against the daemon's uptime and log).

## Proposed outcome

- On every voice turn, the model sees the desktop as it is at that turn:
  the windows that are open, the workspace that is focused, the addresses that
  exist.
- What the persona says about the snapshot is true on every engine.
- "What's open?" and "which workspace am I on?" are answered correctly with no
  `hypr_query` round trip, which is what the persona already asks for.
- A long voice session does not get slower turn by turn from piling up old
  desktop pictures, and the model is not handed several contradictory ones.
- The fix does not cost a perceptible amount of time on each turn.

## Affected users and systems

- Everyone on `[realtime] engine = "local"`, the default.
- `src/omarchy_voice/claude_backend.py` (`WarmBrain`, `_options`, `_turn`) and
  `src/omarchy_voice/local_engine.py` (`LocalSession._answer`), plus
  `planner._system_prompt` and possibly the persona wording in `persona.py`.
- Not the realtime engine, not typed `say`, not the MCP server: those already
  get a fresh snapshot, and must keep doing so.

## Constraints

- **Keep the cached prefix.** The persona, the manifest and the tool schemas
  are about 10k tokens and identical on every turn. The realtime engine learnt
  that rewriting the instructions to change the snapshot re-prefilled all of it
  every turn (`realtime.py:646-663`). The same trap exists here: rebuilding the
  system prompt, or restarting the warm session, to refresh 80 tokens is the
  wrong trade.
- **A failed `hyprctl` must not be shown as an empty desktop.** `live_state()`
  currently does exactly that (`capabilities.py:466`, tracked in #75). Whatever
  delivers the snapshot per turn must not spread that bug to every turn. Either
  this lands after #75, or it carries the fix for `live_state` itself.
- `live_state()` costs about four `hyprctl` forks, around 50 ms. It must not
  sit in front of the model on the turn's critical path any longer than that.
- The policy gate, the confirm flow and the trace are untouched.
- The warm session's existing behaviour (`reset_turn`, cancel, the confirm
  replay in `_local_confirm`) keeps working. The replay bug itself is #76, not
  this.

## Open questions

1. **Where does the snapshot go?** The realtime engine has
   `conversation.item.delete`; the Claude Agent SDK session does not expose one.
   The obvious move is to prefix the user's text with the snapshot on each
   turn. That keeps the prefix cached, but every old snapshot stays in the
   session history. Is that acceptable if the session is already bounded, or
   does this need a mechanism to drop old snapshots, such as restarting the warm
   session after N turns or a size cap?
2. **Should the snapshot also leave the system prompt?** Leaving the 34-hour-old
   one there beside a fresh one gives the model two pictures that disagree.
   Taking it out changes `planner._system_prompt`, which typed `say` and the
   Planner also use. Take it out everywhere, or only for the warm brain?
3. **Order with #75.** Land #75 first so the snapshot this sends every turn is
   never a false "empty desktop", or fold the `live_state` part of #75 into
   this change?
