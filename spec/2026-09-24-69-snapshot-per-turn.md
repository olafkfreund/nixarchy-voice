---
status: draft
issue: 69
intent: intent/2026-09-24-69-snapshot-per-turn.md
---

# Spec: the voice engine must see the desktop as it is now, not as it was at start

## The intent's three open questions, answered

The intent was approved without answers to its three questions, so each is
decided here with the reasoning shown. Any of them can be rejected at this
gate.

### Q1: where does the snapshot go? In the user message, with no history bound yet.

The Agent SDK session has no `conversation.item.delete`, so an old snapshot
cannot be taken back out. Measured on this machine, `live_state()` is **2,235
characters (about 560 tokens) and costs 56 ms** (p50 of 10, max 58 ms).

Each old snapshot left in the history is a cached read on later turns while
the prompt cache is warm, not a fresh prefill. Claude Code compacts the
history on its own when it grows large. So this change adds **no bound of its
own**. The ceiling is stated in the code: about 560 tokens per turn. The
trigger for adding a bound is the `usage` tally `WarmBrain` already keeps
(`claude_backend.py:588`): if uncached input grows turn over turn in a long
session, the next step is to restart the warm session when idle-stop fires
(`local_engine.py:287`), since the cache is cold by then anyway.

Contradictory old pictures are handled by labelling, not by deletion. Each
snapshot says it supersedes every earlier one, and the persona already says
"Never answer from memory of an earlier turn … only the newest snapshot is
true" (`persona.py:56-58`).

### Q2: does the snapshot leave the system prompt? Yes, for every Claude brain.

Both brains take the snapshot per turn, not only the warm one. The cold
`ClaudeBrain` behind typed `say` spawns a new session per call, so its
snapshot was never stale, but moving it too means:

- one code path instead of two;
- the Claude system prompt becomes byte-identical from call to call, which is
  what a prompt cache needs. Today it changes whenever a window title does.

The OpenAI `Planner` (`planner.py:162`) and the realtime engine
(`realtime.py:487`, `:677`) keep what they have. The realtime engine already
does this correctly. The Planner is a fallback, and issue #77 decides its fate.

### Q3: #75 first, or folded in? The `live_state` half is folded in.

This change sends `live_state()` every turn instead of once, so its bug would
now be repeated on every turn: a failed `hyprctl` reads as "no monitors, no
windows" (`capabilities.py:465-478`, where every `query(...)` is followed by
`or []`). Fixing that is part of this spec. The other half of #75,
`_await_new_window` placing an unmatched window (`tools.py:2948-2953`), is
unrelated to the snapshot and stays in #75.

## Design

### 1. `capabilities.live_state()` says "unknown" when it does not know

`query()` already returns `None` for a failure (`_run` returns `""` on
`OSError`, a timeout or no output, and bad JSON gives `None`). Each section
keeps that distinction instead of collapsing it with `or []`:

| Query | Failed (`None`) | Legitimately empty |
|---|---|---|
| monitors | `Monitors: unknown (hyprctl did not answer)` | `Monitors: none` |
| workspaces | `Workspaces in use: unknown (hyprctl did not answer)` | `Workspaces in use: none` |
| activewindow | `Focused window: unknown (hyprctl did not answer)` | line omitted, as now |
| clients | `Open windows: unknown (hyprctl did not answer)` | `Open windows: none` |

If any section is unknown, one closing line says: `Part of this snapshot is
unknown; call hypr_query before acting on what is missing.` That matches the
persona's own fallback rule ("If no snapshot has arrived, call hypr_query",
`persona.py:58-59`). Everything else in the output stays as it is, so the
realtime engine and the MCP resource (`mcp_server.py:218`), which use the
same function, get the fix with no other change.

### 2. `planner._system_prompt(live=True)`

It gains a keyword argument. `live=False` leaves out the
`# The desktop right now` section. The default keeps the Planner unchanged.
`ClaudeBrain._options()` (`claude_backend.py:436`) calls it with
`live=False`.

### 3. One helper puts the snapshot in front of what the user said

```python
def _with_desktop(text: str) -> str:
    return ("# The desktop right now (current as of this turn; "
            "supersedes every earlier snapshot)\n\n"
            f"{capabilities.live_state()}\n\n# What the user said\n\n{text}")
```

The heading is the one the persona already names (`persona.py:50`).

It is called at the two places a user's words enter a session:

- `ClaudeBrain._ask` (`claude_backend.py:498`), the cold path used by typed
  `say`;
- `WarmBrain._turn` (`claude_backend.py:725`), the voice path. It runs through
  `asyncio.to_thread` there, so the 56 ms does not block the event loop.

It is **not** called for the warm-up (`_drain_query`, `:644`). That turn is
meant to carry "no desktop in it" (`:624`).

The confirm replay (`local_engine.py:509`) goes back through `ask_stream`, so
it gets a fresh snapshot too. Replaying a whole utterance is #76's problem.

### 4. What does not change

The persona text: what it says is now true. The policy gate, the hooks, the
tool schemas, the realtime engine, the MCP server, the trace, and the Planner's
prompt.

## Alternatives rejected

- **Rebuild the system prompt or restart the session each turn.** This throws
  away the cached ~6.5k-token prefix and, for a restart, costs a 6.5 s warm-up
  (`local_engine.py:554-557`). The realtime engine tried the equivalent and
  reversed it (`realtime.py:646-663`).
- **Make the model call `hypr_query` every turn.** That is an extra
  continuation (1.5-7.6 s) on every command, which is the cost #71 is trying
  to remove.
- **Serve the snapshot as an MCP resource.** The model has to decide to read
  it, and it believes it already has one.
- **A `UserPromptSubmit` hook returning `additionalContext`.** It puts the same
  bytes in the same place through more machinery, and is harder to test than
  one function at the two send sites.
- **Fix only `WarmBrain`.** It leaves two paths and keeps the cold system
  prompt changing with every window title.
- **Prefetch the snapshot while whisper transcribes.** It would save 56 ms at
  the cost of coupling `LocalSession` to the brain's internals. Not worth it
  until the trace says otherwise.

## Risks

- **Window titles are text anyone can set.** A web page title reaches the
  model through the snapshot. That is already true today, and from the more
  trusted system prompt. Moving it into the user message does not widen it,
  and every action the model takes still passes the policy gate.
- **History growth** in a very long voice session, as bounded and measured in
  Q1.
- **Tests that assert the exact text sent to a fake client.** Those change
  shape, and the plan must list them. `test_claude_backend.py:280` and `:425`
  patch `_system_prompt` with a `return_value`, which accepts the new keyword
  unchanged.
- **Hosts:** none beyond the user service on this machine. There is no Nix,
  config or packaging change.

## Verification

- Unit (`pytest tests -q` in `nix develop`):
  - `live_state` with `_run` faked: each query failing gives "unknown" for
    that section plus the closing line; each legitimately empty result gives
    "none"; a healthy desktop gives the current output unchanged.
  - `ClaudeBrain._options().system_prompt` does not contain
    `# The desktop right now`, and `planner._system_prompt()` still does.
  - `WarmBrain`: two turns with `live_state` faked to return `"A"` then
    `"B"`. Each query sent carries its own snapshot (`A`, then `B`) ahead of
    the user's text.
  - Cold `ClaudeBrain._ask`: the query carries the snapshot.
  - The warm-up query carries no snapshot.
- `nix flake check` passes, as CI runs it.
- On the running daemon, after a rebuild and a restart of `omarchy-voice`:
  open a new window, wait more than a minute, ask "what's open?" by voice.
  The answer names the new window, and `session.log` shows no `hypr_query`
  call for that turn.
