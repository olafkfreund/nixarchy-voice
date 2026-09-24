---
status: approved
issue: 76
spec: spec/2026-09-24-76-confirm-replays-once.md
---

# Plan: confirming a held action runs that action, once, and nothing else

## Approved decisions, carried over from the spec

1. **Release turn, through the model, with the gate narrowed to one call
   (Q1).** Confirming starts a turn whose message names the held call exactly
   (tool plus JSON input). During that turn the gate allows only that call,
   matched on tool and input. Everything else is refused. The approval ends
   with the turn, used or not. We do not execute the stored call ourselves,
   and the hook does not block waiting for the user.
2. **One hold at a time; the rest of the utterance is not resumed (Q2).**
   A second gated action in the same sentence is refused in the release turn,
   not held. The model is told to say what is still undone.
3. **A spoken "confirm" on the local engine is out of scope (Q3).** It is
   filed as **issue #86** ("On the local engine, saying "confirm" cannot
   release a held Claude Code action", open). This plan does not touch it.
   After this change a spoken "confirm" is harmless: `_last_text` is gone, so
   it can never be replayed, and an approval only exists inside a release turn.
4. **`cmd_say` is fixed in the same change (Q4).** It sends the release
   message with `release=True`, never the original text.
5. **The hold keeps the call.** `ClaudeBrain.pending: str | None` stays (the
   description, read by `_held()`, `_settle()`, `cmd_say` and replies).
   `_confirmed: set[str]` is removed. New: `_held_call: tuple[str, dict] |
   None`, set with `pending` in the `NeedsConfirmation` branch, and
   `_approved` (the call the user said yes to, or None).
6. **`confirm()`** moves `_held_call` to `_approved`, clears `pending` and
   `_held_call`, and returns the **release message**, or None if nothing was
   held. The message is a statement about the user: they have just confirmed
   exactly this one call (tool name, JSON input); make that call now,
   unchanged, and nothing else; then say in one sentence whether it ran.
   `cancel()` also clears `_held_call`.
7. **`think(text, *, release=False)` and `WarmBrain.ask_stream(text, *,
   release=False)`** set `_releasing` for the length of the turn and, in a
   `finally`, clear `_releasing` and `_approved`. An approval cannot be spent
   by a turn that was already running when the user confirmed, and cannot
   outlive its release turn.
8. **The gate (`_decide`)** gets a new first branch, before the
   `mcp__omarchy__` allow, replacing the `_confirmed` branch. While
   `_releasing`:
   - a call matching `_approved` (same tool, same input, ignoring Bash's
     `description` key) is allowed once: `_approved = None`, then
     `_dry_run_refusal`, the `CONFIRM` transcript line, `on_action` and
     `_actions`, exactly as today's confirmed branch;
   - a tool in `DRY_RUN_READS` (Read, WebSearch, ToolSearch) takes the
     ordinary path;
   - anything else, including our own tools and a second attempt at the
     spent call, is refused: "Only the call the user confirmed may run now.
     Do not retry anything else; tell the user what is still undone."
   A Write whose content changed no longer matches because its path did not.
9. **An unused approval is said by the brain.** If the release turn ends with
   `_approved` still set, `ask_stream` yields `"<description> was not run."`
   as its last sentence, and `think` appends it to `turn.reply`.
10. **Every call still gets an explicit allow or deny** from `_pre_tool_use`.
    Nothing reaches `can_use_tool`.
11. **Local engine.** `_local_confirm` sends `self.brain.confirm()` as a
    release turn; the reply `Confirmed: {held}` is unchanged. `_answer` gains
    a release keyword passed to `ask_stream`, and logs `release {held}`
    instead of `heard {text!r}`. `_last_text` is deleted. Holds of our own
    tools (`executor.pending`) are untouched.
12. **Not changed:** `realtime.py`, `mcp_server.py`, `Executor`,
    `planner.Planner`, the spoken "Say confirm, or cancel" line, and the
    README's confirmation section.
13. **Sequencing.** This lands after #78 (`fix/69-snapshot-per-turn`) and #81
    (`feat/72-listen-faster`). `_answer`'s signature becomes
    `_answer(text, trace=None, *, release=…)`. The release message arrives
    under #78's "# What the user said" heading, which is why it is worded as
    a fact about the user.
14. **Verification** is unit tests with fakes. Nothing runs on the live
    desktop.

Four details the spec left open, decided here. Each is flagged in the PR:

- **`_answer`'s keyword carries the description, not a bool.** Decision 11
  asks for both a `release` keyword and a `release {held}` log line, and
  `_answer` has no other way to know `held`. So it is
  `release: str | None = None` (the held description), and it passes
  `release=release is not None` to `ask_stream`. The brain side keeps the
  spec's `release: bool`.
- **`_approved` is `(tool, input)`**, the same shape as `_held_call`. The
  "was not run" line uses `describe_tool(*_approved)`, which is the string
  `pending` held, so nothing extra is stored.
- **When the release turn says nothing and the approval is unused**,
  `ask_stream` yields only `"<description> was not run."`, not `"Done."`
  first. When the turn is cancelled (barge-in), nothing is yielded; the
  `finally` still clears the approval.
- **The description match is exact for every tool except Bash**, where only
  the `description` key is ignored. No other key is relaxed (spec, Risks).

## Code as it will be after #78 and #81

Neither PR touches the other's source files. #78 changes
`claude_backend.py`, #81 changes `local_engine.py`. `cli.py` is untouched by
both. Line numbers below are from those branches and were checked:

- `fix/69-snapshot-per-turn:src/omarchy_voice/claude_backend.py`:
  `pending`/`_confirmed` :230-231, `confirm()` :238-243, `cancel()`
  :245-247, `think()` :249-261, `_decide` :264, `mcp__omarchy__` allow
  :272-278, `_confirmed` branch :280-302, `NeedsConfirmation` branch
  :312-319, `_dry_run_refusal` :332, `_pre_tool_use` :364-397, `_ask` :491
  (query at :499, reply fallback :512), `_with_desktop` :558-570,
  `WarmBrain.ask_stream` :715-737, `WarmBrain._turn` :739.
  `DRY_RUN_READS` :101.
- `feat/72-listen-faster:src/omarchy_voice/local_engine.py`: `_last_text`
  :153-154, `_held()` :173-182, `_turn` calls `_answer(text, trace)` :286,
  `_wake_turn` calls `_answer(rest, trace)` :334, `_answer(self, text,
  trace=None)` :376 (`_last_text = text` :385, `heard` log :386,
  `held_before` :388, `ask_stream(text)` :396, "needs confirming" :429-434),
  `_inject` spawns `_answer(text)` :528, `_local_confirm` :531-548 (replay
  :542-546).
- `main:src/omarchy_voice/cli.py`: the Claude-hold branch :166-185,
  `planner.confirm(); turn = planner.think(text)` :175-176.
- `fix/69-snapshot-per-turn:tests/test_claude_backend.py`: `_confirmed`
  readers :103-124, :224-237, :362-373; `WarmBrainTests` :632 (patches
  `_with_desktop` to identity :642-643).
- `feat/72-listen-faster:tests/test_local_engine.py`: `FakeBrain` :27
  (`confirm` :62, `ask_stream` :71), `EngineTestCase.build` :154,
  `test_a_held_action_is_spoken_and_confirm_releases_it` :335-360.
- `main:tests/test_backend_choice.py`: `HeldBrain` :177-203,
  `ConfirmFlowTests` :206-242.

## Steps

0. **Baseline.** On this branch as it stands, `nix develop -c pytest tests
   -q` → record the pass count. It is re-recorded in step 1, after the
   rebase, and that second number is the one step 8 compares against.

1. **Precondition: #78 and #81 are merged.** `gh pr view 78 --json state`
   and `gh pr view 81 --json state` both say `MERGED`. **If either is not
   merged, stop and report. Do not implement against unmerged code.** Then
   `git fetch origin && git rebase origin/main` (this branch carries only the
   intent, spec and plan, so the rebase is clean), and re-run
   `nix develop -c pytest tests -q` → record the new baseline. Check that
   the line numbers in "Code as it will be" still hold with
   `grep -n '_last_text\|def _answer\|def _local_confirm' src/omarchy_voice/local_engine.py`
   and `grep -n '_confirmed\|def confirm\|def ask_stream' src/omarchy_voice/claude_backend.py`.
   If something else merged in between and moved them, use the new numbers;
   if it changed the code they describe, stop and report.

2. **Regression tests first, and they must fail on main.**
   `tests/test_local_engine.py`, a new `ReleaseTurnTests(EngineTestCase)`.
   It uses a `ScriptedBrain(WarmBrain)` defined in the test file:
   - `ScriptedBrain(Config(dry_run=False), session.executor)`. The brain gets
     its own config because `EngineTestCase.build` hard-codes
     `dry_run=True` and cannot take it as an override (:155). The session's
     executor is shared, so transcript and actions land in one place.
     `_client` is set to a sentinel so `ask_stream` does not answer
     `NO_SESSION`.
   - `_turn(self, text)` is overridden (so `_with_desktop` and the SDK never
     run). It looks `text` up in a script `{utterance: [bash command, …]}`.
     Text not in the script is a release message, and the model "makes" the
     calls in `self.release_calls` (default `[("Bash", {"command":
     "reboot"})]`). Every call goes through the real `_pre_tool_use`. An
     allow appends the command to `self.allowed`. Then it yields `"ok."`.
     Nothing is executed.
   - Script: `"commit my notes and reboot"` → `["git -C ~/notes commit -am
     wip", "reboot"]`; `"what time is it"` → `["date"]`; `"reboot now"` →
     `["reboot"]`.
   - **`test_confirm_runs_the_held_action_once_not_the_utterance`** (the
     spec's run A). Inject "commit my notes and reboot" with
     `session._answer`, then `await session._local_confirm()`, then
     `await asyncio.gather(*session._tasks)`. Expect
     `brain.allowed == ["git -C ~/notes commit -am wip", "reboot"]`. On main
     it is the commit twice, then reboot.
   - **`test_confirm_after_another_utterance_runs_only_the_held_action`**
     (the spec's run B). Commit-and-reboot, then "what time is it", then
     confirm. Expect `allowed` to be `[commit, "date", "reboot"]`, with
     `date` once. Then an ordinary "reboot now" turn:
     it is **denied and held again**, which proves the approval did not
     survive the release turn. On main, `allowed` ends `"date", "date"`, no
     reboot, and the later reboot is allowed because the approval is still
     armed.
   - `tests/test_backend_choice.py`:
     **`test_confirming_sends_the_release_not_the_utterance`**.
     `HeldBrain.confirm()` returns `"RELEASE reboot the machine"`, and
     `HeldBrain.think(text, release=False)` records `(text, release)`.
     Expect the second call to be `("RELEASE reboot the machine", True)`. On
     main it is `("reboot the machine", ...)`, and `think` is called without
     `release`.
   → verify: `nix develop -c pytest tests/test_local_engine.py
   tests/test_backend_choice.py -q -k "once_not_the_utterance or
   another_utterance or sends_the_release"` → **3 failed, 0 passed** on the
   rebased main, each on the assertion described (not on an import
   or attribute error). Paste the failure lines into the PR.

3. **`src/omarchy_voice/claude_backend.py`: the hold keeps the call.**
   - `__init__` (:230-231): keep `pending`; replace `_confirmed` with
     `_held_call = None`, `_approved = None`, `_releasing = False`, with a
     comment that an approval exists only inside the release turn.
   - A module function `_release_message(tool, tool_input) -> str` next to
     `describe_tool`: decision 6's wording, with
     `json.dumps(tool_input, default=str)` uncut. A module function
     `_same_call(tool, tool_input, approved) -> bool`: tool equal, and
     inputs equal after dropping `description` from both when the tool is
     Bash.
   - `NeedsConfirmation` branch (:312-319): also
     `self._held_call = (tool, dict(tool_input))`.
   - `confirm()` (:238-243): `call, self._held_call, self.pending = …, None,
     None`; `self._approved = call`; return `_release_message(*call)` if
     `call` else None. Docstring: the user said yes; the next *release turn*
     may make that exact call once.
   - `cancel()` (:245-247): also clears `_held_call`.
   → verify by step 6.

4. **`claude_backend.py`: the release turn and the gate.**
   - `_decide` (:264): the new first branch from decision 8, above :272.
     Delete the `_confirmed` branch (:280-302), moving its comments about
     dry-run and logging to the new branch. Update the docstring's order:
     release turn first, then ours, then deny and confirm, then dry-run.
   - `_unspent()` helper: `f"{describe_tool(*self._approved)} was not run."`
     if `_approved` else `""`.
   - `think(self, text, *, release=False)` (:249): set `_releasing =
     release` before `asyncio.run`, and wrap the existing body so that
     before `turn.elapsed` is set, `if release and (left := self._unspent()):
     turn.reply = f"{turn.reply} {left}".strip()`, then in `finally`
     `_releasing = False; _approved = None`.
   - `WarmBrain.ask_stream(self, text, *, release=False)` (:715): same
     setting and `finally`. After the existing `try/except/else`, if
     `release` and `_unspent()`, yield it. In the `else`, when nothing was
     spoken and the approval is unused, skip the `"Done."` fallback.
   → verify by step 6.

5. **`src/omarchy_voice/local_engine.py` and `cli.py`: send the release.**
   - `_answer(self, text, trace=None, *, release: str | None = None)`
     (:376): delete `self._last_text = text` (:385). The log line (:386) is
     `f"release {release}"` when `release` is set, else
     `f"heard   {text!r}"`. Pass `release=release is not None` to
     `self.brain.ask_stream` (:396).
   - Delete `_last_text` and its comment (:153-154).
   - `_local_confirm` (:539-548): `text = self.brain.confirm()`, then
     `self._spawn(self._answer(text, release=held))`. Replace the "replayed"
     comment with one saying the brain sends that one call and the gate
     allows nothing else. `_settle()` and `Confirmed: {held}` are unchanged.
   - `cli.py:175-176`: `turn = planner.think(planner.confirm(),
     release=True)`. Update the comment at :166-170 ("re-plays the
     instruction") to "asks the brain to make that one call".
   - `FakeBrain.ask_stream(self, text, release=False)` (test file :71)
     records `release` in `self.released`.
   → verify: `grep -rn '_last_text\|_confirmed' src tests` prints nothing.

6. **Tests** (beside step 2's regressions).
   - `tests/test_claude_backend.py`, new `ReleaseTurnGateTests`. Each drives
     `gate()` inside a release turn by setting `subject._releasing = True`
     after `confirm()`, and one test goes through `think(…, release=True)`
     with `_ask` patched to push calls through `_pre_tool_use`:
     - `test_the_held_call_runs_once_in_its_release_turn`: allow, then the
       identical call is denied with "Only the call the user confirmed".
     - `test_nothing_else_runs_in_a_release_turn`: `Bash ls` and
       `mcp__omarchy__notify` are denied. `ToolSearch` is allowed.
     - `test_a_second_gated_action_is_refused_not_held`: `poweroff` during
       the release of `reboot` is denied, and `pending` stays None.
     - `test_an_approval_is_not_honoured_outside_a_release_turn`: confirm,
       then the call with `_releasing` False → held again, not allowed.
     - `test_an_unused_approval_dies_with_its_turn`: `think("x",
       release=True)` whose model makes no call → `_approved is None` and
       `turn.reply` ends "reboot was not run." The same through
       `ask_stream` in `WarmBrainTests` (a scripted `FakeClient` answer to
       the release message): the last sentence is "reboot was not run." and
       there is no "Done.".
     - `test_a_write_with_other_content_does_not_match`: held Write
       `{file_path: /tmp/a, content: "x"}`, release attempt with content
       `"y"` → denied. `test_bash_description_is_not_part_of_the_match`:
       held `{command: reboot, description: "Reboot"}`, released with
       `description: "Restart"` → allowed.
     - `test_the_release_message_names_the_exact_call`: `confirm()` returns
       text containing `"Bash"` and `json.dumps({"command": "reboot"})`, and
       returns None when nothing is held.
     - Rewrite `test_a_held_action_goes_through_once_the_user_says_yes`
       (:103-107), `test_saying_yes_once_does_not_approve_it_forever`
       (:109-124), `test_saying_yes_does_not_make_a_dry_run_act` (:224-237)
       and `test_a_confirmed_run_is_logged_like_any_other` (:362-373)
       against release turns and `_approved`. The dry-run one keeps its
       assertion: refused with "would have run", and `_approved is None`
       (the approval is spent).
   - `tests/test_local_engine.py`:
     `test_a_held_action_is_spoken_and_confirm_releases_it` (:335-360) now
     expects `brain.asked == ["close the browser", "omarchy reboot"]` (the
     second is what `FakeBrain.confirm()` returns) and
     `brain.released == [False, True]`. A new
     `test_confirm_logs_release_not_heard`: the log has `release omarchy
     reboot`, and no second `heard` line.
   - `tests/test_backend_choice.py`: `HeldBrain.think` takes `release`
     (:200); the three existing `ConfirmFlowTests` pass unchanged.
   - **Mutation checks**, each then restored:
     (a) drop `self._approved = None` from the matching branch →
     `test_the_held_call_runs_once_in_its_release_turn` and run A fail;
     (b) drop the `self._releasing` condition from the matching branch →
     `test_an_approval_is_not_honoured_outside_a_release_turn` fails;
     (c) in `_local_confirm`, spawn `_answer` with the old utterance instead
     of `confirm()`'s message → both step 2 regressions fail.
   → verify: `nix develop -c pytest tests -q` equals the step-1 baseline
   plus the new tests, no new failures, and step 2's three tests pass.

7. **Whole check.** `nix flake check --no-write-lock-file` → it passes.

8. **Live dry run, read-only.** `nix run .#omarchy-voice -- -n say
   --no-confirm "reboot"` → the reply says it is held and `waiting confirm
   to run: reboot` prints; nothing runs (dry run, no confirm). This checks
   the hold path still works end to end with the real CLI. The release turn
   itself is not driven live: confirming under `-n` is refused by the
   dry-run gate by design, and confirming without it would reboot.

9. **PR.** Push and open a PR that closes #76 and links the intent, spec and
   plan. The description lists the four decisions above that the spec left
   open, pastes step 2's failing lines from main, and says #86 is untouched.

## Tests

```
nix develop -c pytest tests -q                                              # baseline + new, 0 new failures
nix develop -c pytest tests/test_local_engine.py tests/test_backend_choice.py -q -k "once_not_the_utterance or another_utterance or sends_the_release"   # 3 fail on main, pass after
nix develop -c pytest tests/test_claude_backend.py tests/test_local_engine.py tests/test_backend_choice.py -q
nix flake check --no-write-lock-file                                        # CI parity
```

Plus the mutation checks in step 6 and the dry run in step 8.

## Deviations during implementation

None changes an approved spec decision.

- **Only a release turn clears `_approved` (decision 7, step 4).** Taken
  literally, "in a `finally`, clear `_releasing` and `_approved`" clears it
  at the end of *every* turn. On the local engine the user can confirm while
  an ordinary turn is still running. That turn's `finally` then threw the
  yes away before the queued release turn could use it. So `_releasing` is
  still reset on every turn, but `_approved` is cleared only when
  `release=True`. An approval still never outlives its release turn. It is
  only honoured while `_releasing`, and every release turn is preceded by
  the `confirm()` that sets it. `ask_stream`'s `NO_SESSION` return moved
  inside the `try`, so that path clears it too. New test:
  `test_a_turn_ending_after_the_yes_does_not_throw_it_away`.
- **Mutation (a)** fails `test_the_held_call_runs_once_in_its_release_turn`
  (and four others), but not run A. Run A's scripted model makes the
  release call once, so it never tries a second time for the missing
  `_approved = None` to allow.
- **Mutation (c)** fails run B and
  `test_a_held_action_is_spoken_and_confirm_releases_it`, but not run A.
  The old utterance, sent as a release turn, has its commit refused by the
  narrowed gate. The gate alone is enough for run A, which is the point.
- **Mutation (b)**, as "honour `_approved` outside `_releasing`", fails
  `test_an_approval_is_not_honoured_outside_a_release_turn` (and two
  others).
- **Tests added beyond step 6:** `test_the_release_turn_through_think`,
  `test_cancel_forgets_the_held_call`, the one above, and a `ScriptedBrain`
  that subclasses `WarmBrain`. The Write test uses `/tmp/reboot.txt`,
  because only a held Write can be released, and a path is held only when it
  matches a confirm pattern. `ConfirmFlowTests._run` gained a `backend`
  argument so the test can read the brain it built.
- **A cancel after confirm withdraws the approval (deviation 4, from lead
  review of a75551e).** The first deviation lets an approval wait for its
  release turn. A cancel in that gap found no `pending`, answered "nothing
  to cancel", and the queued release turn ran the cancelled action. Now
  `ClaudeBrain.cancel()` also clears `_approved` and returns whichever
  description was set. A new `held_or_approved` property tells
  `_local_cancel` there is something to cancel. This enforces the spec's
  rule that a cancel ends an approval, and changes no spec decision. Test:
  `test_a_cancel_after_confirm_withdraws_the_approval`. It failed on a75551e
  with "nothing to cancel", and the action ran. The mutation that keeps
  `_approved` in `cancel()` fails it.
- **Step 8** held `AskUserQuestion {... "Reboot the machine now?" ...}`, not
  `reboot`: the model asked through that tool, and its JSON matches
  `\breboot\b`. The hold path works end to end and nothing ran. This is the
  ordinary gate, unchanged here. It is noted, not fixed.

## Rollback

It is a single squash-merged PR, with no Nix, config or schema change and no
persisted state (`pending`, `_held_call` and `_approved` live in memory and
die with the daemon). `git revert <merge>` restores the utterance replay and
`_confirmed`, and with them the double-run bug. There is no switch to turn it
off without reverting. If the release turn fails closed too often in
practice ("<held> was not run."), the fix is to relax matching for a
specific input key with evidence, not to revert.

Out of scope: issue #86 (a spoken "confirm" cannot release a Claude Code
hold on the local engine). It needs its own design, because it decides
whether a transcript counts as consent.
