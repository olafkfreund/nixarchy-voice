---
status: approved
issue: 77
intent: intent/2026-09-24-77-engine-duplication.md
---

# Spec: after realtime goes, one home for the hold wording, and nothing else to merge

Line numbers are from `origin/main` at `50dcf99`, re-verified on 2026-09-25.
The first draft was written against `2172c4f`; since then #121, #110, #111,
#79, #80, #114 and #138 have merged. Issue #77 is OPEN; #110, #114 and #121
are CLOSED (checked with `gh issue list` on 2026-09-25).

#121 (spec `spec/2026-09-24-121-realtime-engine-future.md`, approved) decided
to **remove** the OpenAI realtime engine, and that is done: `realtime.py` was
deleted in `4866d44`. Everything below is measured on the code after that
removal.

## Correction to the intent

The intent's test counts are from before #121. On `50dcf99`, with
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`:

- `nix develop -c pytest tests -q` gives `1085 passed, 782 subtests passed`.
- Per file (`pytest --collect-only -q`, grouped): `test_planner.py` 12,
  `test_claude_backend.py` 108, `test_local_engine.py` 104, `test_mcp.py` 19.
  `test_realtime.py` and `test_realtime_wire.py` are gone with #121.

## What #121 already removed from #77's table

The intent's inventory, re-read on `50dcf99`, where `realtime.py` is gone:

| Concern | In the intent (`6a9a3f5`) | On `50dcf99`, after #121 | Left for #77 |
| --- | --- | --- | --- |
| Prompt assembly | 3 assemblers, 3 extra texts | `planner._system_prompt` (planner.py:109-114), reused by `claude_backend.py:564`. `LOCAL_PERSONA` (local_engine.py:135) is appended by the `LocalBrain` subclass (local_engine.py:197-201). `persona.PERSONA` (persona.py:7) is the only other persona text | nothing |
| Tool-schema conversion | 3 converters | `to_chat_tools` (planner.py:95-106) and `_to_mcp_tools` (mcp_server.py:93-108). There is one per wire format, and none is a copy of the other | nothing (see Alternatives) |
| Gate tools | `GATE_TOOLS` (realtime.py:265) and `GATE_SCHEMAS` (mcp_server.py:55) | `GATE_SCHEMAS` only (mcp_server.py:55-90) | nothing |
| Hold wording | 4 strings in 4 files | 3 strings in 3 files: tools.py:1816-1818, mcp_server.py:120-124, claude_backend.py:72-75 | **co-locate** |
| Session plumbing | `LocalSession` and `RealtimeSession` | `LocalSession` only | nothing |
| Helpers `local_engine.py:43` imports | 5, from realtime.py | moved into `local_engine.py` by #121 (:48, :53, :56, :65, :80). `local_engine.py` no longer imports `realtime` | nothing |

So #77 becomes one small, behaviour-preserving change. Its other items are
either resolved by #121 or filed separately (decision 2).

`claude_backend.py:426-429` has a fourth "another route around it" sentence.
It is the **deny** message (`Refused: ...`), not a hold, so it is out of scope.

## Decisions on the intent's open questions

The owner approved the intent with a bare "approve all", so this spec decides
each question. Each decision can be rejected at this gate.

### 1. Wait for #121, or approve with both scopes? **Wait, and take the removal scope only.**

#121 decided removal (its decision 1) and has merged. This spec therefore
describes one scope: the one that is left after removal. The
"three copies merged" scope is dropped rather than kept as a contingency.
Writing a merge plan for a module that is being deleted is work that would be
thrown away.

The helpers (`ECHO_TAIL_SECONDS`, `WATCH_POLL_SECONDS`, `_run_until_done`,
`watch_headline`, `watch_message`) moved in #121, not here, and are now at
local_engine.py:48-80.

**Sequencing:** #121, #114 and #110 have all merged, so the intent's
sequencing constraint is met. Nothing else open is known to touch these three
modules' hold wording.

### 2. Are the side items part of #77? **No. Each becomes its own issue or nothing.**

- **The stale `wtype` checks are a real bug, and belong in a separate issue.**
  doctor's `hands` section (cli.py:504) prints `✗ wtype` on a machine without
  it, but nothing uses wtype (tools.py:3737-3749). `capabilities.missing_tools()`
  (capabilities.py:1234-1235) also lists it, but it has **no callers**: grep
  finds its definition only. Fixing it changes doctor output, and #77 must not
  change anything a user sees. The recommended issue: "doctor reports wtype,
  which nothing uses; `missing_tools()` is dead". The lead files it. I do not
  file issues in this role.
- **The `gpt-4.1` default for `planner_model` (config.py:272)** is a product
  choice about `say`'s fallback model. It is not duplication. It goes to a
  separate issue if the owner wants it changed.
- **The `Executor.describe` if-chain (tools.py:2064-2176)** is one function,
  not a copy of anything. It is dropped from #77. Nobody has reported a bug
  in it, so no issue is recommended.

### 3. Unify MCP's `phrase` with realtime's `heard_phrase`? **Nothing to unify.**

`heard_phrase` existed only in `realtime.GATE_TOOLS`, which #121 deleted. The
only `confirm_last` left is MCP's (mcp_server.py:56-77), with `phrase`. That
is the schema external MCP clients already call, and it is **not changed**.
The intent's outcome, "there is one `confirm_last`/`cancel_last` contract",
is met by the deletion.

## Design

### The one change: the hold wordings live together in `tools.py`

Today, what a model is told when an action is held is written in three
places. Each place is a different caller with a different consent rule, and
each difference is deliberate:

| Caller | Consent rule | Where the wording is now |
| --- | --- | --- |
| bare `Executor` (`say` planner) | the CLI prompts the user | tools.py:1816-1818, in `Executor.__init__` (tools.py:1808) |
| MCP client | agent asks in the conversation, then `confirm_last(phrase)`, with the phrase checked against `confirm_words` and `CONFIRM_DELAY` | mcp_server.py:120-124, in `build_server` |
| Claude Code brain (`say` and the local engine) | the engine hears consent, not the model (#86) | claude_backend.py:72-75 `HOLD_INSTRUCTION`, used at :435 and :562 (in `ClaudeBrain._options`, :532, which `WarmBrain._options`, :765, extends) |

The change:

1. In `tools.py`, just above `class Executor`, add three module constants.
   Each carries the text it has today, byte for byte, with a one-line
   comment naming the consent rule it goes with:
   - `SPOKEN_HOLD_INSTRUCTION`, the current default.
   - `MCP_HOLD_INSTRUCTION`, the current mcp_server.py:120-124 sentence.
   - `HOLD_INSTRUCTION`, the current claude_backend.py:72-75 fragment.
2. `Executor.__init__` (tools.py:1816) assigns `SPOKEN_HOLD_INSTRUCTION`.
3. `mcp_server.build_server` (:120) assigns `MCP_HOLD_INSTRUCTION`.
4. `claude_backend.py` drops its own definition and does
   `from .tools import HOLD_INSTRUCTION`. `claude_backend.HOLD_INSTRUCTION`
   keeps resolving, so :435, :562 and any importer are unchanged.

(The first draft had a step 5, rewording the mcp_server.py comment that
pointed at `realtime.GATE_TOOLS`. #121 already reworded it: mcp_server.py:51-54
no longer names realtime. The step is dropped.)

The strings are not merged into one sentence. The intent's constraint says
the wordings "differ on purpose", and the table above shows why. The single
place is one block, with the consent rule written next to each wording. That
is the intent's outcome: "written down next to it rather than in a separate
copy".

### Why this is worth doing, and not only tidying

`tests/test_mcp.py:70-85`, `test_over_mcp_it_asks_in_the_conversation_instead`,
calls `build_server` but then asserts on **its own hand-typed copy** of the
MCP sentence (:77-80). That copy is already different from the real one at
mcp_server.py:120-124. As a result, removing "another route around it" from
the real MCP wording (the clause the test's own comment calls the one "that
closed the shell workaround") passes all 1085 tests on `50dcf99`. This was measured; see Verification. Once the wording is
a constant, the test asserts on the real text. It passes an `Executor` into
`build_server(config, executor)` (mcp_server.py:111 already takes one) and
reads `executor.confirm_instruction`.

### Not changed

- `ClaudeBrain.pending` stays separate from `executor.pending`
  (claude_backend.py:284-290). `LocalSession._held`, `_consent`, `_release`
  and `_local_cancel` (local_engine.py:273, :822, :868, :909) are untouched.
  They are the one place that reads both gates, and the intent's constraint
  keeps them apart.
- `to_chat_tools` and `_to_mcp_tools` stay unchanged.
- `LOCAL_PERSONA` (local_engine.py:135) stays where it is. #121 already
  reworded its comment (local_engine.py:120-134) to contrast it with
  `persona.PERSONA` instead of `REALTIME_PERSONA`.

## Alternatives rejected

- **The three-engine merge the intent sketched** (a shared persona builder, a
  shared converter, one `confirm_last` for realtime and MCP, and a
  `LocalSession`/`RealtimeSession` base). #121 removes the third engine, so
  most of this merge becomes deletion. The rest would be abstraction with a
  single user.
- **One converter with a `format=` switch** for chat and MCP. The two
  functions share no lines beyond reading `name` and `description`, and they
  build different types (a dict, and `mcp.types.Tool`). A switch adds a
  branch and saves nothing. Each is the single translation into its own wire
  format, so there is no copy to drift.
- **One hold sentence for every caller.** This would weaken or confuse
  consent. An MCP agent told "confirm out loud" gets stuck or looks for a way
  around the gate (the tools.py:1812-1815 comment). The Claude brain told to
  "call confirm_last" would ask the model to release its own hold, which #86
  forbids.
- **A new `consent.py` for three strings.** That is a new module for about 15
  lines. `tools.py` already owns `Executor.confirm_instruction`, which every
  caller sets.
- **Folding in the wtype, gpt-4.1 and `describe` items** (decision 2). The
  wtype fix changes doctor output, and the other two are not duplication.
  #77 must be provably behaviour-preserving.

## Risks

- **A string changes by a character during the move.** This is caught by the
  characterization tests below, which are written and passing on main before
  the move.
- **An import cycle.** `claude_backend` and `mcp_server` already import from
  `tools` (`attach_waker`, `Executor`, `tools_for`). `tools` imports neither,
  so this adds no cycle.
- **Rebase conflicts.** #114, #110 and #121 have merged, so the ones the
  first draft feared are gone. The diff here is about 20 lines.
- **Hosts.** p620 and razer see no difference. No config key, output line or
  tool schema changes.

## Verification

### How "behaviour-preserving" is proven

1. **Characterization tests first, green on main.** Before any code moves,
   add tests to `tests/test_mcp.py` and `tests/test_claude_backend.py` that
   pin each wording to its exact current text, copied from main:
   - `Executor(Config()).confirm_instruction` equals the tools.py:1816-1818
     text.
   - `build_server(Config(), executor)` leaves
     `executor.confirm_instruction` equal to the mcp_server.py:120-124 text.
   - `WarmBrain._options()` leaves `executor.confirm_instruction` equal to
     `"This action " +` the claude_backend.py:72-75 text. The `can_use_tool`
     hold message at :435 ends with the same fragment.
   They are committed and pass on main. They then pass unchanged after the
   move. A test that passes on both sides of a refactor, and pins the exact
   output, is the proof of preservation.
2. **Existing tests pass unedited**, except `test_mcp.py:70-85`. It is
   rewritten to assert on the real `build_server` output instead of its local
   copy. It only gets stricter.
3. **Nothing else observable changes.** `tools_for(Config())` names,
   `_to_mcp_tools` output, `to_chat_tools` output and
   `planner._system_prompt(live=False)` are compared before and after with a
   throwaway script, with `capabilities.manifest` stubbed. The sha256 of each
   must be equal. The script is not committed. Its output goes in the PR
   description.
4. **Same count.** Every existing test still runs, and the new
   characterization tests are the only additions. The plan pins the exact
   total against main at the time it is written (1085 on `50dcf99`).

### No test fails on main first, because no behaviour changes

This refactor preserves behaviour, so there is no new behaviour for a test to
fail on. Instead, the gap is closed by a test that the **mutation** below
proves is new coverage.

**Measured on main (`50dcf99`, re-run 2026-09-25 in a scratch copy):** I
removed "Do not try another route around it." from the real MCP wording
(mcp_server.py:123-124) and ran `nix develop -c pytest tests -q`. The result
was `1085 passed`. (The first draft measured the same on `2172c4f`, 1119
passed.) After the change here, the same mutation fails both the
MCP characterization test and the rewritten `test_mcp.py:70-85`.

### Mutation checks

Each is done by hand on the implementation branch and reverted:

- Change one character in each of the three constants: the matching
  characterization test fails.
- Make `build_server` assign `SPOKEN_HOLD_INSTRUCTION`: the MCP
  characterization test and the rewritten `test_mcp.py` test fail.
- Make `claude_backend` define its own `HOLD_INSTRUCTION` that differs from
  `tools.HOLD_INSTRUCTION`: the Claude characterization test fails.

### Runs

All runs export `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`:

- `nix develop -c pytest tests -q`: all pass.
- `nix develop -c python3 -m unittest discover -s tests`: the same count, OK.
- `nix flake check --no-write-lock-file`: passes.
- `grep -n "HOLD_INSTRUCTION\|confirm_instruction = (" src/omarchy_voice/*.py`:
  string literals appear only in `tools.py`.
