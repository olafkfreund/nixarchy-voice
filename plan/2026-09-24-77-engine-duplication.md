---
status: approved
issue: 77
spec: spec/2026-09-24-77-engine-duplication.md
---

# Plan: after realtime goes, one home for the hold wording, and nothing else to merge

Closes #77. Base: `origin/main` `50dcf99` (1085 passed, 782 subtests). Every
`file:line` below was checked against `50dcf99` on 2026-09-25 (the branch
`refactor/77-engine-duplication` has no `src/` or `tests/` diff from it).
Line numbers are in `src/omarchy_voice/` unless a `tests/` path is named.
Only `tools.py`, `mcp_server.py`, `claude_backend.py`, `tests/test_mcp.py` and
`tests/test_claude_backend.py` change. `local_engine.py`, `planner.py`,
`persona.py`, `cli.py`, `capabilities.py` and `flake.nix` do not change.

## Approved decisions, carried over from the spec

1. **Scope is what is left after #121 removed the realtime engine**
   (`realtime.py` deleted in `4866d44`). The "three copies merged" scope is
   dropped, not kept as a contingency. #121, #114 and #110 have merged, so
   the intent's sequencing constraint is met.
2. **Nothing else in the intent's inventory is changed.** Prompt assembly
   (`planner._system_prompt` `planner.py:109-114`, reused at
   `claude_backend.py:564`; `LOCAL_PERSONA` `local_engine.py:135`, appended
   at `local_engine.py:197-201`; `persona.PERSONA` `persona.py:7`), the
   converters `to_chat_tools` (`planner.py:95-106`) and `_to_mcp_tools`
   (`mcp_server.py:93-108`), `GATE_SCHEMAS` (`mcp_server.py:55-90`) and the
   helpers #121 moved to `local_engine.py:48-80` all stay as they are.
3. **The side items are not part of #77.** The stale `wtype` check (doctor
   `cli.py:504`, and the caller-less `capabilities.missing_tools()`
   `capabilities.py:1234-1235`) is now **issue #143 (OPEN), out of scope
   here**: fixing it changes doctor output. The `gpt-4.1` `planner_model`
   default (`config.py:272`) and the `Executor.describe` if-chain
   (`tools.py:2064-2176`) are not duplication and are dropped.
4. **MCP's `confirm_last(phrase)` (`mcp_server.py:56-77`) is not changed.**
   `heard_phrase` died with `realtime.GATE_TOOLS`, so there is one contract.
5. **The three hold wordings stay three different strings.** Each goes with a
   different consent rule, and the difference is deliberate:
   - bare `Executor` (`say` planner): the CLI prompts the user;
   - MCP client: the agent asks in the conversation, then calls
     `confirm_last(phrase)`, checked against `confirm_words` and
     `CONFIRM_DELAY`;
   - Claude Code brain (`say` and the local engine): the engine hears
     consent, not the model (#86).
   One sentence for all was rejected: it would weaken or confuse consent.
6. **They live together as three module constants in `tools.py`, just above
   `class Executor` (`tools.py:1805`)**, each with a one-line comment naming
   its consent rule, each carrying today's text **byte for byte**:
   - `SPOKEN_HOLD_INSTRUCTION` = today's `tools.py:1816-1818`:
     `"This action needs spoken confirmation. Stop here and ask the user to confirm out loud; do not try another route around it."`
   - `MCP_HOLD_INSTRUCTION` = today's `mcp_server.py:120-124`:
     `"This action needs the user's confirmation. Stop here and ask them in this conversation. When they answer, call confirm_last with their own words, or cancel_last if they decline. Do not try another route around it."`
   - `HOLD_INSTRUCTION` = today's `claude_backend.py:72-75` (a fragment,
     used after a description or after `"This action "`):
     `"needs the user's confirmation, which they give the engine directly. Stop here. Say it is waiting; do not ask them to confirm and do not say confirm, go ahead or yes do it."`
   No new module (`consent.py` was rejected): `tools.py` already owns
   `Executor.confirm_instruction`, which every caller sets.
7. **`Executor.__init__` (`tools.py:1816-1818`) assigns
   `SPOKEN_HOLD_INSTRUCTION`.** The comment above it (`:1812-1815`) stays.
8. **`mcp_server.build_server` (`mcp_server.py:120-124`) assigns
   `MCP_HOLD_INSTRUCTION`**, imported beside `attach_waker, Executor,
   tools_for` (`mcp_server.py:32`). The comment at `:117-119` stays.
9. **`claude_backend.py` drops its definition (`:70-75`, comment and
   assignment) and imports `HOLD_INSTRUCTION` from `.tools`** on the existing
   line `:38`. `claude_backend.HOLD_INSTRUCTION` still resolves, so `:435`
   (the hold message in `_decide`, `:363`), `:562` (in `ClaudeBrain._options`,
   `:532`, extended by `WarmBrain._options`, `:765`) and any importer are
   unchanged. The #86 comment moves with the constant into `tools.py`.
10. **No import cycle.** `claude_backend` (`:38`) and `mcp_server` (`:32`)
    already import from `tools`; `tools` imports neither.
11. **Not changed:** `ClaudeBrain.pending` stays apart from `executor.pending`
    (`claude_backend.py:284-290`); `LocalSession._held`, `_consent`,
    `_release`, `_local_cancel` (`local_engine.py:273`, `:822`, `:868`,
    `:909`) are untouched; the deny message at `claude_backend.py:426-429`
    (`Refused: ...`) is not a hold and is out of scope.
12. **Proof of preservation:** characterization tests that pin each wording
    byte for byte are written first and pass on main, then pass unchanged
    after the move. The only existing test edited is
    `tests/test_mcp.py:69-85`, which today asserts on its own hand-typed
    copy (`:77-80`, already different from the real text) and is rewritten
    to read the real `build_server` output. It only gets stricter.
13. **The gap this closes, measured on `50dcf99`:** deleting "Do not try
    another route around it." from the real MCP wording passes all 1085
    tests. After this change the same mutation fails.
14. **Nothing else observable changes:** `tools_for(Config())` names,
    `_to_mcp_tools` output, `to_chat_tools` output and
    `planner._system_prompt(live=False)` hash the same before and after, via
    a throwaway script (not committed; output in the PR description).
15. **Rejected, not to be reintroduced:** the three-engine merge (shared
    persona builder, shared converter, a session base class); one converter
    with a `format=` switch; one hold sentence for every caller; a
    `consent.py`; folding in the wtype, `gpt-4.1` or `describe` items.

## Spec ambiguities, resolved here (flagged for the reviewer)

- **A. The expected texts in the tests are literals, not the constants.** A
  test that imports `tools.MCP_HOLD_INSTRUCTION` and compares against it
  would pass under every mutation of the constant. The characterization
  tests hold their own copy of each text from decision 6, so they are the
  independent pin the spec asks for.
- **B. "`WarmBrain._options()`" is tested through the existing
  `options_of` helper (`tests/test_claude_backend.py:421-431`)**, on both
  `ClaudeBrain` and `WarmBrain` (subtests). That helper mocks
  `mcp_server.build_server`, so the test pins the Claude wording alone; the
  order "build_server first, then the Claude wording" (`:554-562`) is
  unchanged code and is not re-tested. `WarmBrainTests.setUp`
  (`:1002-1012`) is not used, since it stubs `ClaudeBrain._options`.
- **C. The `:435` hold message is pinned by suffix.** Its head is the
  tool's description, which is not what #77 moves. The test asserts the
  hook's reason ends with `" " + <HOLD text>`.
- **D. Mutations: the lead asked for "delete a sentence", the spec for
  "change one character".** Both are run (step 6).
- **E. Test count.** Four new tests (two in each file); the rewritten
  `test_mcp.py` test keeps its name. 1085 → **1089**, and 782 → **784**
  subtests (the `ClaudeBrain`/`WarmBrain` pair).

## Landing order and overlaps

**#77 lands first** in this batch (#77, #83, #91), all of which touch
`tools.py`. It is the smallest (about 20 lines in `src/`), so the others
rebase over a small, well-located hunk.

- **#83** (`feat/83-services-and-mcp`, intent and spec only): edits
  `tools.py` at `READ_ONLY_TOOLS` (`:58-60`), the schemas (`:884-900`,
  `:1340-1360`), `describe` (`:2102-2105`) and dispatch (`:4514-4515`). None
  is inside `:1805-1818`, so no textual conflict; only its line numbers
  after `:1805` move by the constants' block (about +15).
- **#91** (`feat/91-atspi-app-content`, intent and spec only): `tools.py`
  around `_ocr_region` (`:2814`) and `mcp_server.py:169`. No conflict; its
  `tools.py` line numbers move by the same offset.
- **#129** (`fix/129-tui-route-policy`, intent only) is `tools.py` policy:
  `_BARE_PROGRAM_RE` (`:391`), `_pane_runs_command` (`:715-727`),
  `omarchy_runs_command` (`:1746-1772`) and `Executor._call_locked`
  (`:1890-1925`). The nearest hunk to ours is `:1746-1772`, about 30 lines
  above the insertion point. No conflict expected; if #129 lands first, the
  line numbers here are re-checked (step 1).
- **#139** (`fix/139-tools-while-speaking`, intent only) touches
  `claude_backend.py` around `_pre_tool_use`/`_decide` (`:481-503`, `:423`)
  and `:532-593`. #77 edits `claude_backend.py:38` and `:70-75` only; the
  hold line `:435` and `:562` are not edited. No conflict expected, but the
  #139 implementer should know `HOLD_INSTRUCTION` now lives in `tools.py`.

## Steps

0. **Baseline.** On `refactor/77-engine-duplication`, `git status` is clean
   and `git merge-base HEAD origin/main` is `50dcf99`. `gh issue view 77`
   shows OPEN. With `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`,
   run `nix develop -c pytest tests -q` and
   `nix develop -c python3 -m unittest discover -s tests`
   → verify by **1085 passed, 782 subtests passed**, and the same count OK
   from unittest. Record any failure that already exists before continuing.
1. **Precondition: nothing touching these files has merged ahead of #77.**
   `git fetch origin` and `git log --oneline 50dcf99..origin/main --
   src/omarchy_voice/tools.py src/omarchy_voice/mcp_server.py
   src/omarchy_voice/claude_backend.py tests/test_mcp.py
   tests/test_claude_backend.py`. If empty, continue. If not,
   `git rebase origin/main`, repeat step 0, and re-check every `file:line`
   here, including that the three texts still match decision 6
   → verify by a clean rebase and a green baseline, with corrections to this
   file in the same commit as the code.
2. **Baseline hashes (decision 14).** Write
   `scratchpad/plan-77/observe.py` (not committed): it prints the sha256 of
   `sorted(t["name"] for t in tools_for(Config()))`, of
   `repr(_to_mcp_tools(tools_for(Config())))`, of
   `json.dumps(to_chat_tools(tools_for(Config())), sort_keys=True)` and of
   `planner._system_prompt(live=False)`, with `capabilities.manifest`
   patched to return a fixed string. Run it with the DBUS export under
   `nix develop` and save the output as `before.txt`
   → verify by four hashes, identical over two runs.
3. **Characterization tests, on unchanged `src/`.**
   - `tests/test_mcp.py`, class `ConfirmWordingTests` (`:57`):
     1. `test_the_voice_wording_is_pinned`:
        `assertEqual(Executor(Config()).confirm_instruction, <spoken text>)`.
     2. `test_the_mcp_wording_is_pinned` (`@skipIf(mcp is None, ...)`):
        `executor = Executor(Config())`, `mcp_server.build_server(Config(),
        executor)`, `assertEqual(executor.confirm_instruction, <MCP text>)`.
     - Rewrite `test_over_mcp_it_asks_in_the_conversation_instead`
       (`:69-85`): drop the throwaway `build_server(Config())` call and the hand-typed copy (`:73-80`), pass an `Executor`
       into `build_server` as in 2, and keep its three assertions
       (`NotIn "out loud"`, `In "confirm_last"`, `In "another route around
       it"`) against the real `executor.confirm_instruction`.
   - `tests/test_claude_backend.py`, class `SpokenConsentGateTests` (`:434`):
     3. `test_the_claude_wording_is_pinned`: for `cls` in `(ClaudeBrain,
        WarmBrain)`, in a subtest: `config = Config(dry_run=True)`,
        `subject = cls(config, Executor(config))`, `options_of(subject)`,
        `assertEqual(subject.executor.confirm_instruction, "This action " +
        <Claude text>)`.
     4. `test_the_hold_message_is_pinned`:
        `result = gate(brain(dry_run=False), "Bash", {"command": "reboot"})`,
        `assertEqual(result.behavior, "deny")`,
        `assertTrue(result.message.endswith(" " + <Claude text>))`.
   The texts are literals copied from decision 6 (ambiguity A).
   → verify by `nix develop -c pytest tests/test_mcp.py
   tests/test_claude_backend.py -q` **passing on main** (all four new tests
   and the rewritten one). If one fails here, the test is wrong: fix the
   test, never `src/`. Then apply the spec's measured mutation (delete "Do
   not try another route around it." from `mcp_server.py:123-124`), run the
   two files, see test 2 and the rewritten test fail, and revert with
   `git checkout -- src/`. Commit the tests alone:
   `test(hold): pin the three hold wordings before they move (#77)`.
4. **`tools.py`: the constants (decisions 6, 7).** Insert the block of three
   constants, with their consent-rule comments and the #86 comment on
   `HOLD_INSTRUCTION`, between `_misused_launch_browser` (ends `:1802`) and
   `class Executor` (`:1805`). `Executor.__init__` `:1816-1818` becomes
   `self.confirm_instruction = SPOKEN_HOLD_INSTRUCTION`
   → verify by test 1 and `tests/test_mcp.py:59-67` passing.
5. **`mcp_server.py` and `claude_backend.py` (decisions 8, 9).**
   `mcp_server.py:32` adds `MCP_HOLD_INSTRUCTION` to the `.tools` import;
   `:120-124` becomes `executor.confirm_instruction = MCP_HOLD_INSTRUCTION`.
   `claude_backend.py:38` adds `HOLD_INSTRUCTION` to the `.tools` import;
   `:70-75` are deleted
   → verify by `nix develop -c pytest tests/test_mcp.py
   tests/test_claude_backend.py -q` passing with **no test edited since
   step 3**, and `python3 -c "from omarchy_voice import claude_backend,
   tools; assert claude_backend.HOLD_INSTRUCTION is tools.HOLD_INSTRUCTION"`
   under `nix develop`.
6. **Mutation checks.** Apply each alone, run
   `nix develop -c pytest tests/test_mcp.py tests/test_claude_backend.py -q`,
   revert with `git checkout -- src/`:
   - M1: delete `"This action needs spoken confirmation. "` from
     `SPOKEN_HOLD_INSTRUCTION` → test 1 fails.
   - M2: delete `" Do not try another route around it."` from
     `MCP_HOLD_INSTRUCTION` → test 2 and the rewritten test fail.
   - M3: delete `" Stop here."` from `HOLD_INSTRUCTION` → tests 3 (both
     subtests) and 4 fail.
   - M4: change one character in each constant in turn → its test fails.
   - M5: `build_server` assigns `SPOKEN_HOLD_INSTRUCTION` → test 2 and the
     rewritten test fail.
   - M6: `claude_backend` defines its own `HOLD_INSTRUCTION` differing by
     one word from `tools.HOLD_INSTRUCTION` → tests 3 and 4 fail.
   → verify by each mutation turning its tests red, and `git diff --stat`
   showing only the five intended files after each revert.
7. **Nothing else observable changed (decision 14).** Re-run
   `observe.py` into `after.txt`; `diff before.txt after.txt`
   → verify by no output. Paste both into the PR description.
8. **Scope check.**
   `grep -n "HOLD_INSTRUCTION\|confirm_instruction = (" src/omarchy_voice/*.py`
   → verify by string literals only in `tools.py` (the three definitions),
   and plain assignments/uses in `tools.py:18xx`, `mcp_server.py`,
   `claude_backend.py:435` and `:562` (shifted by the deletion).
   `git diff --stat origin/main` lists only the five files named above plus
   this plan.
9. **Full suites and the flake.** With
   `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`:
   `nix develop -c pytest tests -q`,
   `nix develop -c python3 -m unittest discover -s tests`,
   `nix flake check --no-write-lock-file`
   → verify by **1089 passed, 784 subtests passed**, unittest `Ran 1089
   tests ... OK`, and the flake check passing. Commit the `src/` change
   alone: `refactor(hold): the three hold wordings live together in tools.py (#77)`.

## Tests

```
export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent
nix develop -c pytest tests/test_mcp.py tests/test_claude_backend.py -q   # green on main (step 3) and after (step 5)
nix develop -c pytest tests -q                                            # 1089 passed, 784 subtests passed
nix develop -c python3 -m unittest discover -s tests                      # Ran 1089 tests, OK
nix flake check --no-write-lock-file                                      # passes
```

No test runs the Claude CLI or a desktop command: `options_of` stubs the SDK
and `build_server`, and `gate` only reads a verdict. Nothing runs in the
background. Scratch files stay in `scratchpad/plan-77/`.

## Rollback

Two commits on `refactor/77-engine-duplication`: the tests, then the move.
Before merge, drop the branch. After merge, `git revert <move sha>` restores
the three in-place strings and the characterization tests stay green, since
the texts are identical both ways. Reverting the test commit too returns to
`50dcf99` exactly. No config key, output line, tool schema or cache format
changes, so p620 and razer need nothing beyond the normal rebuild.
