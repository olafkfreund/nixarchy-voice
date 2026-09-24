---
status: approved
issue: 76
author: olafkfreund
---

# Intent: confirming a held action must run that action, once, and nothing else

Closes #76.

## Problem

When a Claude Code tool call (Bash, Write, …) matches a confirm pattern, the
brain's gate holds it as a description, not as something re-executable
(`src/omarchy_voice/claude_backend.py:312-319`). Confirming it on the local
engine (`listen confirm`, the keybind — `local_engine.py:449-450`) does two
things (`local_engine.py:494-511`):

```python
self.brain.confirm()
...
# A Claude Code tool call has no re-executable handle here, so the
# instruction is replayed with that exact action pre-approved.
self._spawn(self._answer(self._last_text))
```

`confirm()` (`claude_backend.py:238-243`) puts the held description in
`_confirmed`, a one-shot pass for that exact string (`claude_backend.py:281-302`).
Then the *whole* last utterance is sent to the brain again. Everything else
in it goes through the gate a second time as if new: other Claude Code calls
are allowed again by policy, and our own tools (`mcp__omarchy__*`) are allowed
at this hook unconditionally (`claude_backend.py:272-278`) — a `type_text` or
a workspace move repeats just the same.

`_last_text` is also simply "the last thing heard" (`local_engine.py:348`,
set on every `_answer`, typed turns included). If anything was said between
the hold and the confirm, the *wrong* utterance is replayed, the held action
is never reattempted, and its one-shot pass stays in `_confirmed` — to be
spent by whichever later call happens to match it, with no confirmation.

Demonstrated against the real `LocalSession` and the real `ClaudeBrain` gate
(`_pre_tool_use`). Only the model is scripted: for each utterance it makes the
Bash calls a model would, through the hook. An "allow" is counted, nothing is
executed:

```
held:           reboot
confirm says:   Confirmed: reboot
brain asked:    ['commit my notes and reboot', 'commit my notes and reboot']
allowed:        ['git -C ~/notes commit -am wip', 'git -C ~/notes commit -am wip', 'reboot']

held:           reboot
confirm says:   Confirmed: reboot
brain asked:    ['commit my notes and reboot', 'what time is it', 'what time is it']
allowed:        ['git -C ~/notes commit -am wip', 'date', 'date']
still approved: {'reboot'}
```

First run: the commit is allowed twice. Second run: "what time is it" is
answered twice, the reboot the user confirmed never runs, and the pass for
`reboot` is left armed.

What the demonstration does not prove is what a real model does with the
replay. The warm session remembers the first turn, so it *may* notice it
already committed — but the replay arrives as a plain repeat of the user's
words, with nothing saying it is a replay or which part was approved, so
nothing makes that reliable. The engine's contract is the bug: it asks for
the whole utterance again and the gate would let all of it through.

### What "confirm" means elsewhere

- **Realtime engine:** no Claude Code brain; every hold is our own tool,
  parked in `Executor.pending` as a re-executable `(name, args)`. Both the
  keybind (`realtime.py:945-953`) and the model's `confirm_last` with the
  heard phrase checked against `confirm_words` (`realtime.py:1279-1300`) call
  `Executor.run_pending`, which runs exactly that one call. No replay; not
  affected.
- **MCP server:** `confirm_last` checks the phrase and `CONFIRM_DELAY`
  (`mcp_server.py:131-150`), then `Executor.run_pending`
  (`mcp_server.py:160-165`). One call, no replay; not affected. (Claude Code
  tools used by an MCP client are gated by that client, not by us.)
- **Local engine, our own tools:** held in `Executor.pending` and released by
  `run_pending` (`local_engine.py:496-501`). Not affected.
- **One-shot `omarchy-voice say` with the Claude brain:** the same replay
  (`cli.py:171-180`: `planner.confirm()` then `planner.think(text)`), and
  there `think` opens a fresh session every time (`claude_backend.py:497-498`),
  so the replayed turn has no memory of the first at all.

## Proposed outcome

- Confirming a held Claude Code action runs that action once. Nothing else
  from the utterance that held it runs again.
- A confirmation always refers to the action that was held, whatever has been
  said since.
- A pass that is not used for the action it was given for does not outlive
  the confirmation.
- Local-engine holds of our own tools, the realtime engine and the MCP server
  behave exactly as they do now.

## Affected users and systems

- `src/omarchy_voice/local_engine.py` — `_local_confirm`, `_last_text`.
- `src/omarchy_voice/claude_backend.py` — `ClaudeBrain.confirm`, `pending`,
  `_confirmed`; the hold keeps a description only.
- `src/omarchy_voice/cli.py` — `cmd_say`'s identical replay.
- Anyone on the default (local) engine who confirms a held shutdown, reboot,
  `nixos-rebuild`, `omarchy update`/`pkg`/`install`, … that was asked for in
  the same breath as something else.

## Constraints

- The gate must not weaken: a held action still needs a confirmation that
  does not come from the model or the transcript (`_local_confirm`'s own
  docstring), and dry-run still refuses on the confirmed path
  (`claude_backend.py:293-294`).
- Every PreToolUse call still gets an explicit allow or deny
  (`_pre_tool_use` docstring) — nothing may fall through to `can_use_tool`.
- Tests use a fake brain/executor; nothing is executed on the live desktop.

## Open questions

1. **How does the held action run?** Options: execute it directly from
   stored `(tool, input)` (Bash and Write are replayable; the SDK tool is
   not ours to call); or keep going through the model but send a message that
   asks for *only* that action, stated as such; or have the brain hold the
   whole turn open instead of denying. Which is acceptable, given Claude Code
   is what runs its own tools?
2. **Several holds in one utterance** — the gate keeps one `pending` and the
   model is told to stop at the first; is one-at-a-time still the rule?
3. **Spoken "confirm" on the local engine.** The prompt says "Say confirm, or
   cancel" (`local_engine.py:397`), but the only route to `brain.confirm()`
   is the control verb; a spoken "confirm" reaches the model as text and,
   by reading the code, cannot release a Claude Code hold — it becomes the
   new `_last_text` that a later keybind confirm would replay. Fix here, or a
   separate issue?
4. Fix `cmd_say` in the same change, or leave the one-shot path to its own
   issue?
