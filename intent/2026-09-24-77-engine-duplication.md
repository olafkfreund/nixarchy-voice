---
status: draft
issue: 77
author: olafkfreund
---

# Intent: one copy of the prompt, tool conversion and confirm gate per concern

Closes #77. **Depends on #121.** Whether the OpenAI realtime engine stays
decides whether this merges three copies or two. See Open questions.

## Problem

The same machinery is written out once per way of reaching the model:

- the chat `Planner` (the `say` fallback);
- the Claude brain (`say`, and the local engine's `run`);
- the OpenAI realtime engine;
- the MCP server.

Each copy drifts on its own. A consent or prompt fix has to find and change
every copy. #86, #113 and #114 each ended up in more than one file for this
reason.

Line numbers are from `origin/main` at 6a9a3f5.

### Prompt assembly (3 assemblers, 3 extra persona texts)

| Where | What | Lines |
| --- | --- | --- |
| `planner.py:109-114` `_system_prompt` | `PERSONA` + manifest (+ live state) | 6 |
| `claude_backend.py:564` | reuses `planner._system_prompt(live=False)` | shared |
| `realtime.py:509-518` `_instructions` | `PERSONA` + `REALTIME_PERSONA` + manifest + live state, assembled again | 10 |
| `realtime.py:177-254` `REALTIME_PERSONA` | spoken-reply rules | 78 |
| `local_engine.py:71-88` `LOCAL_PERSONA`, appended at :132 | spoken-reply rules for the local engine | 18 |
| `persona.py:7` `PERSONA` | the shared persona (file is 211 lines) | shared |

`local_engine.py:52-69` explains that `LOCAL_PERSONA` reverses one of
`REALTIME_PERSONA`'s rules. Realtime acts first and narrates afterwards. The
local engine announces first. That makes two texts for the same thing: how
she speaks out loud. Some of the difference is measured and deliberate.

### Tool-schema conversion (3 converters)

| Where | Output shape | Lines |
| --- | --- | --- |
| `planner.py:95-106` `to_chat_tools` | chat-completions `{"type","function":{...}}` | 12 |
| `realtime.py:301-313` `to_realtime_tools` | realtime `{"type","name",...}` + `GATE_TOOLS` | 13 |
| `mcp_server.py:93-108` `_to_mcp_tools` | MCP `Tool(inputSchema=...)` | 16 |

All three rename `input_schema` for a different wire format. They are small.
The cost is that there are three of them, and gate tools are added in two
different places.

### Confirm/hold gates (4 mechanisms, 2 wordings)

| Where | What | Lines |
| --- | --- | --- |
| `realtime.py:265-298` `GATE_TOOLS` | `confirm_last`/`cancel_last`, `heard_phrase` arg; confirm always refused since #113 | 34 |
| `mcp_server.py:55-90` `GATE_SCHEMAS` | `confirm_last`/`cancel_last`, `phrase` arg; phrase checked against confirm phrases | 36 |
| `claude_backend.py:290` `ClaudeBrain.pending` | a held description, separate from `executor.pending` on purpose (:285-289) | — |
| `local_engine.py:202-211` `_held`, `_consent` :707, `_release` :753, `_local_cancel` :794 | reads both gates | ~100 |
| `realtime.py:258` and `claude_backend.py:72` `HOLD_INSTRUCTION` | two different wordings of "this is held" | 4 + 4 |

The two `confirm_last` schemas share a name but differ in argument name and
behaviour. A model that learns one learns the other wrongly.

### Session plumbing copied between engines

`LocalSession` (`local_engine.py:138`) repeats parts of `RealtimeSession`
(`realtime.py:415`). Measured with `difflib` line similarity:

| Method | realtime.py | local_engine.py | Similarity |
| --- | --- | --- | --- |
| `_control` | 880-906 (27) | 641-667 (27) | 1.00, identical |
| `_set_active` | 910-923 (14) | 671-687 (17) | 0.71 |
| `_settle` | 1255-1265 (11) | 213-227 (15) | 0.54 |
| `_watch_loop` | 605-627 (23) | 611-631 (21) | 0.50 |
| confirm/cancel | 954-972 (19) | 749-807 (59) | 0.28 |
| `_wake_loop` / `_wake_turn` | 557-604 (48) | 354-399 (46) | 0.06, same job, different code |

`local_engine.py:43` already imports five helpers from `realtime.py`. The
local engine therefore depends on the realtime module whether or not
realtime is kept.

### Also listed in #77, re-measured

- The `say` fallback `Planner` still defaults to `planner_model = "gpt-4.1"`
  (config.py:264).
- `tools.py` is now 4,695 lines (the issue said 4,095).
  `Executor.describe` is a single if-chain at tools.py:2046-2159
  (~114 lines).
- `capabilities.missing_tools()` still lists `wtype` (capabilities.py:1213),
  and so does doctor (cli.py:519). tools.py:3711-3722 says wtype is gone.

## Proposed outcome

- A change to how she speaks, what "held" means, or how a tool is described
  to a model is made in one place and reaches every engine that is kept.
- There is one `confirm_last`/`cancel_last` contract. Each engine's
  different consent rule (key-only on realtime, engine-checked phrase
  elsewhere) is written down next to it rather than in a separate copy.
- No user-visible behaviour changes. The existing tests on every engine pass
  unchanged, apart from tests that only assert where the code is.

## Affected users and systems

- `planner.py`, `claude_backend.py`, `local_engine.py`, `realtime.py` (if
  kept), `mcp_server.py`, `persona.py`, `tools.py`, `capabilities.py`,
  `cli.py`.
- Tests: `test_planner.py` (12), `test_claude_backend.py` (108),
  `test_local_engine.py` (85), `test_mcp.py` (19), `test_realtime.py` (96),
  `test_realtime_wire.py` (2).
- Users: nobody should notice. p620 runs the local engine (no `engine` set in
  `~/.config/nixos/hosts/p620/nixos/nixarchy.nix`).

## Constraints

- **Sequencing.** The issue asks for this to go last, after the behaviour
  issues, so their diffs stay small. Still open and touching the same files:
  #114 (speech while the mic is open) and #110 (deny rules and compose
  panes). #121 must be decided first.
- There must be no change to consent semantics. #113's key-only release on
  realtime, #86's engine-checked phrase and `ClaudeBrain.pending` being kept
  apart from `executor.pending` (claude_backend.py:285-289) are all
  deliberate. Merging them must not make them the same rule.
- The two `HOLD_INSTRUCTION` wordings differ on purpose today. Any single
  source still has to say different things per engine.
- p620's config is read-only and must work unchanged.
- The work has to be split into reviewable pieces. It must not be one diff
  across nine modules.

## Open questions

1. **Scope depends on #121's outcome.** If realtime is removed, the realtime
   column goes. What is left is two converters (chat, MCP), one extra
   persona and the helpers `local_engine.py` imports from `realtime.py`.
   These must move somewhere, and that becomes part of this issue or of
   #121. If realtime is kept or frozen, all three copies are merged, and the
   `LocalSession`/`RealtimeSession` plumbing is in scope. Should this intent
   wait for #121's approval, or should it be approved now with both scopes
   stated?
2. Are the side items (the `gpt-4.1` planner default, the `describe`
   if-chain, stale `wtype` checks) part of this issue, or separate small
   issues? The `wtype` one is a real doctor bug. It would report a missing
   tool that nothing uses.
3. Should the MCP server's `confirm_last` argument (`phrase`) and the realtime
   one (`heard_phrase`) be unified? That changes a tool schema that external
   MCP clients may already call.
