---
status: approved
issue: 71
spec: spec/2026-09-24-71-answer-without-the-model.md
---

# Plan: a common command must not wait for the model

## Approved decisions, carried over from the spec

1. **The eval gates the change (Q6).** The bar has two numbers: all 11
   measured fixed commands are routed against the fixture windows, and
   nothing else is routed. "Nothing else" means every other logged line and
   every sentence in the adversarial table (decision 13). The bar is enforced
   in unit tests and in the replay script, and a later grammar change has to
   keep both numbers.
2. **Exact hits only (Q1).** A hit runs through the `Executor` and is spoken.
   Anything else goes to Claude exactly as it does today. There is no
   shortlist for near misses. If the replay shows near misses are common, a
   shortlist becomes a follow-up issue.
3. **Only the measured routes (Q2).** They are: workspace N, open a terminal,
   list the windows, and focus/move/close one named open window. No volume,
   brightness, media, lock, screenshot, nightlight or theme. **"Open <app>" is
   not routed** ("today's weather" clear-matches GNOME Weather). Only "open a
   terminal" is routed, as the fixed Omarchy route `omarchy launch terminal`.
4. **No learned cache (Q3)**, now or as a planned follow-up.
5. **The local engine only (Q4).** The router is called from
   `LocalSession._answer`, so spoken turns and `omarchy-voice listen say`
   (`_inject`) both get it. It is not added to the realtime engine, to the
   one-shot `omarchy-voice say`, which stays the model's benchmark, or to
   MCP.
6. **Speak the result only, one short line (Q5).** Examples: "Workspace
   one.", "Opening a terminal.", "Teams.", "Moved Discord to workspace
   three.", "Closed Weather.", or the window list. A failure speaks the
   error. A hold speaks the existing "… needs confirming" line.
   `LOCAL_PERSONA` is unchanged.
7. **`src/omarchy_voice/router.py`:** `route(text, clients) -> Route | None`.
   `Route` is a dataclass with `tool`, `args`, `said` and `answer`. `answer`
   is set only for the window list, which runs nothing. `clients` is a
   zero-argument callable, called only after a pattern that needs windows has
   matched.
8. **Normalising, in order:** lowercase and strip surrounding quotes. Then
   require exactly one sentence: split on `.?!`, and more than one non-empty
   piece gives `None`. Then turn commas into spaces and remove politeness:
   "can/could/would/will you", "please", "for me", "just", plus leading
   "okay", "so", "right", "now", the wake word and "hey <wake word>".
9. **Refusals, before any pattern:** any of `don't, do not, not, never, no,
   stop, cancel, undo, without, except, and, then, but` gives `None`.
10. **Patterns are `re.fullmatch` over the whole sentence, never a search,**
    tried in this order:

    | Kind | Pattern (sketch) | Call |
    |---|---|---|
    | workspace | `[(switch\|go\|change) [back] to] workspace N` | `hypr_dispatch {dispatcher: "focus", args: {workspace: "N"}}` |
    | terminal | `open [up] [a] [new] terminal` | `omarchy_cli {command: "launch terminal"}` |
    | list | `what windows (do i have\|are\|have i got) open[ed] [on (this\|my\|the) desktop]` | none: answered from the client list |
    | move | `move [the\|my] NAME [window\|app] [back] to workspace N` | `hypr_dispatch {dispatcher: "window.move", args: {workspace: "N", follow: false, window: "address:…"}}` |
    | close | `close [the\|my] NAME [window\|app]` | `hypr_dispatch {dispatcher: "window.close", args: {window: "address:…"}}` |
    | focus | `(bring up\|focus\|switch to\|go to) [the\|my] NAME [window\|app]` | `hypr_dispatch {dispatcher: "focus", args: {window: "address:…"}}` |

    `N` is 1-10, as digits or words. `follow` is `false`, because "move
    Discord" does not say "and take me there".
11. **NAME resolves to exactly one open window at any score, or nothing.** It
    reuses `_rank_windows`, and it is stricter than `_resolve_window`, which
    accepts a unique top score. NAME must not be one of `it, this, that, them,
    this one, that one, everything, all, window, windows`. A title-only match
    is allowed (PWAs have hashed classes) for focus and move only; close
    needs a class match (see Deviations, 2026-09-24). A failed client query
    (`_query_rows` returns an error) gives `None`. The router never uses
    `_query_json` (#75).
12. **The window list is spoken as names from `_short_class`**, with
    duplicates counted, e.g. "Open: Discord, Microsoft Teams (PWA), Weather,
    and two Alacritty." There are no toggle or direction routes. The module
    docstring states the rule for adding one: a toggle says "toggled", never
    "on" or "off", and a direction word is part of the pattern, never a
    wildcard.
13. **The adversarial sentences must return `None`:** "Don't close it.", "Do
    not switch to workspace two.", "Turn it down.", "Close it.", "Close the
    window.", "Move it to workspace 2.", "Close the terminal.", "Focus the
    terminal.", "Close chrome.", "Close all windows.", "Switch to workspace 3
    and open firefox.", "Close Discord and Teams.", "Close weather, no,
    discord.", "What is the weather for today?" and "Can you open up today's
    weather?". So must the logged command-word non-commands: "Plus
    terminal.", "I will turn the light.", "Opened it.", "Perfect. Close it.",
    "Oh, that's fine. Close the window.", "Go back to works, bit one.", "Can
    you show me today's weather please?" and "That's fine. You can stop it.
    You can close the terminal. This was a test." "Bring up Teams." and
    "Close the weather window." are routed when exactly one window matches.
14. **Hook in `_answer`, after `heard` is logged and the trace is started,
    before `brain.ask_stream`.** It is skipped when `self._held()` is not
    `None`, and when `config.router` is false. `router` is a new
    `bool = True` in `Config`, documented in `share/config.example.toml`. On
    `None`, the turn continues exactly as today. A call runs via
    `await asyncio.to_thread(self.executor.call, tool, args)`, so the policy
    gate, holds, denies, `on_action` and the trace `TOOL` spans all apply
    unchanged. A hit logs `routed  <description>`. The spoken line is
    `route.said` if `result.ok`, the `[dry-run] would run: …` output in
    dry-run, and otherwise the error. The router then shares the turn's
    existing tail: `end_turn()`, closing the trace, the held check and
    `_settle()`. The router works when Claude is down, because it never
    touches the brain.
15. **`WarmBrain.note(line)`** appends to `self._notes`. `_turn` puts the
    notes in front of the text it sends, then clears them, under the heading
    `# Done without you since your last turn (already run, do not repeat)`,
    one line each: `- User said "<text>" → <call> → <outcome>`. With #78,
    the notes go in front of the snapshot block, and the user's text stays
    under "What the user said". A held route's note says "held for
    confirmation". The notes are lost on a session rebuild, and nothing is
    added for that.
16. **`tools/eval_router.py`** replays the `heard` lines of `session.log`
    (read only) and prints `ROUTE` or `model` per line plus a total. It uses
    a built-in fixture client list, or `hyprctl clients -j` with `--live`. It
    imports `router.route` and never calls the `Executor`. The log is not
    committed. The unit tests carry the 11 command lines and the quoted
    non-commands.
17. **Order against open PRs.** #85 must merge before this lands. No app
    matching is added here, and `find_apps`/`clear_match` are not
    duplicated. #81 and #78 overlap in `_answer` and `WarmBrain._turn`. They
    mean small textual merges, and whichever lands first, the other rebases.
18. **Rejected, and not to be built:** a near-miss shortlist, routing "open
    <app>" through `clear_match`, a learned cache, the unmeasured routes, and
    Omarchy binds or `omarchy commands --json` as the vocabulary. Also
    `rapidfuzz` and Model2Vec, `_resolve_window` as the resolver, the router
    inside `Executor` or `ask_stream`, and dropping the persona's
    announcement.

No Nix or packaging change: `router.py` is plain Python in the existing
package, and `nix/package.nix` uses `lib.cleanSource ../.`.

### Details the spec left open, decided here (the approver should check these)

- **A. What a failure speaks.** `Executor` results that fail are written to
  the model, not to a person. A deny reads `refused: <why>. Tell the user you
  will not do that.`, and a hold's output is `confirm_instruction` ("Stop
  here and ask the user…"). Speaking those verbatim would read an
  instruction meant for the model aloud. So:
  - a **hold** speaks nothing of its own. The tail's existing "<held> needs
    confirming. Say confirm, or cancel." is the only line, as decision 6
    says.
  - any **other failure** speaks the first sentence of `result.output` (up to
    the first `". "`), e.g. "refused: matched a deny rule" or "warning:
    window not found".
  - **dry-run** speaks `result.output` whole.
- **B. A release turn must reach the brain.** (Reshaped 2026-09-24, see
  Deviations.) #76 replaced the confirm replay: `_local_confirm` now calls
  `self._answer(text, release=held)` with the brain's structured release
  message, and `_last_text` is gone. That turn still goes through
  `_answer`, and nothing is held by then, so the router would be tried on
  it. If it routed, the approval would never be spent by the brain. So
  `_answer` skips the router whenever `release` is set:
  `hit = await self._route(text) if release is None else None`. No new
  keyword; the `routable` parameter is gone. Test: a release turn whose text
  is "switch to workspace one" reaches the brain with `release=True` and no
  router note. Mutation: remove the release skip → that test fails.
- **C. Spoken window names.** `_short_class` returns the title for a PWA and
  the raw class otherwise, and on this machine the raw class is often
  reverse-DNS (`org.gnome.Weather`, `org.omarchy.herdr`, checked with
  `hyprctl clients -j`). The router's spoken name is `_short_class(c)`. When
  that is the raw class, it is cut to the part after the last dot. So
  GNOME Weather is "Weather", as the spec's example line reads.
- **D. The wake word.** `route` cannot know the wake word from
  `route(text, clients)` alone. The signature becomes
  `route(text, clients, wake="")`, and `_answer` passes
  `self.config.wake_word`.
- **E. The client query runs off the loop.** `_query_rows` is a subprocess
  with a 5 s timeout. The hook calls
  `await asyncio.to_thread(router.route, …)` instead of calling `route`
  inline, so a hung `hyprctl` cannot stall the speech loop. A router
  exception is logged as `warn    router: …` and treated as `None`: the model
  gets the turn, which is today's behaviour.
- **F. "Close the terminal".** With the spec's fixture, no window's class or
  title contains "terminal" (they are `Alacritty`), so this returns `None`
  because nothing matches, not because two windows match. The two-matches
  rule gets its own case: "close alacritty" with two Alacritty windows
  returns `None`.
- **G. The live log has grown.** It has 123 lines containing `heard` today
  (the spec measured 114). The live bar is therefore: the 11 Q6 lines are
  `ROUTE`, and every other `ROUTE` line is listed in the PR and checked by
  hand against decision 3. The fixed 11/0 bar is enforced by the unit tests,
  not by a growing file.

## Steps

0. **Baseline.** `nix develop -c pytest tests -q` → record the pass count.
   Run it **after** step 1, on the rebased branch, since the rebase brings in
   other PRs' tests.

1. **Precondition: land order.** Check with `gh pr view 85 --json state`
   (and the same for 81 and 78).
   - **If #85 is not merged: stop and report.** Do not implement. Decision
     17 makes it a hard dependency.
   - Once #85 is merged: `git fetch origin && git rebase origin/main` on
     `feat/71-answer-without-the-model`. This branch only has docs commits,
     so the rebase is clean.
   - Every later step is written against the code **as it is after #85,
     #81 and #78**. Line numbers are cited per branch:
     `tools.py` from `feat/70-find-what-is-installed`, `local_engine.py`,
     `config.py` and `share/config.example.toml` from
     `feat/72-listen-faster`, and `claude_backend.py` from
     `fix/69-snapshot-per-turn`. Find each place by its name, because
     rebasing moves the numbers.
   - **If #81 or #78 is still open**, implement anyway and report which one.
     The differences are small:
     - without #81, `_answer(self, text)` has no `trace` parameter. Add
       `routable` as its only keyword, and keep `task = Trace() if …`.
     - without #78, `_turn` sends `text` directly (there is no
       `_with_desktop`). The notes go in front of `text`.
     Whichever of these PRs lands after this one takes the merge.
   → verify: `git log --oneline origin/main..HEAD` shows only this issue's
   commits, and `grep -n "def _resolve_app" src/omarchy_voice/tools.py` hits.

2. **`src/omarchy_voice/config.py`: the switch.** Add `router: bool = True`
   right after `trace_timings` (`config.py:406` on `feat/72-listen-faster`).
   Add a comment: routes the measured fixed commands before the model
   (#71), off turns every sentence back to the model with no rebuild.
   → verify: `Config().router is True`.

3. **`share/config.example.toml`: document it.** Add it to `[hands]`, after
   `trace_timings = false` (`:194` on `feat/72-listen-faster`), as
   `router = true`. It gets a three-line comment: four fixed commands
   (workspace N, open a terminal, what windows are open, focus/move/close one
   named window) are run without asking Claude, and only when the sentence
   is exactly one of them. `[hands]` is not in `PREFIXED_SECTIONS`
   (`config.py:195`), so the key loads as `router`.
   → verify: `nix develop -c python3 -c "from pathlib import Path; from
   omarchy_voice.config import load; c = load(Path('share/config.example.toml'));
   assert c.router is True and 'router' not in c.unknown_keys"`.

4. **`src/omarchy_voice/router.py` (new): the matcher.**
   - Module docstring: what it routes and why only these (decision 3), the
     exactly-one-window rule, and the toggle/direction rule (decision 12).
     Add a `ponytail:` line naming the ceiling: a phrase table and six
     regexes. New routes come from `tools/eval_router.py` showing them in
     the log, one row and one test each.
   - `@dataclass(frozen=True) class Route: tool: str | None; args: dict;
     said: str; answer: str | None = None`.
   - `NUMBERS` (`one`…`ten` → `1`…`10`). `REFUSE` is the word set from
     decision 9, with `don't` / `do not` matched as words after
     normalising. `PRONOUNS` is the set from decision 11.
   - `_normalise(text, wake) -> str | None` implements decision 8, and
     returns `None` for more than one sentence.
   - `_spoken(client) -> str`: detail C.
   - `_one_window(clients_fn, name) -> dict | None`: `None` if `name` is in
     `PRONOUNS`. Call `clients_fn()`, which returns `(rows, error)`, and
     return `None` on an error. Then `ranked = _rank_windows(rows, name)`
     (`tools.py:548` on `feat/70-find-what-is-installed`), and return the
     window only if `len(ranked) == 1`.
   - `route(text, clients, wake="") -> Route | None`: normalise. Refuse on
     decision 9 words. Try the six `re.fullmatch` patterns in decision 10
     order, and build `Route`s with the args shown there, using
     `f"address:{window['address']}"`. The `said` lines: "Workspace
     <word>.", "Opening a terminal.", "<Name>.", "Moved <Name> to workspace
     <word>.", "Closed <Name>.".
   - The list route: `rows, error = clients()`. On an error, return `None`
     (never "nothing is open"). With no rows, the answer is "Nothing is
     open." Otherwise it is "Open: " plus the `_spoken` names, counted with
     `collections.Counter` ("two Alacritty") and joined with commas and a
     final "and".
   - It imports `_rank_windows` and `_short_class` from `.tools`. `tools.py`
     itself is not changed.
   → verify by step 8.

5. **`tools/eval_router.py` (new): the replay.** In the style of
   `tools/verify_matching.py`: shebang, a docstring saying it reads
   `session.log`, never calls the `Executor`, and cannot act, and
   `sys.path` set to `src`.
   - `FIXTURE_CLIENTS`: Discord (`class discord`, `0x1`), Teams
     (`class chrome-<32 hex>-Default`, title `Microsoft Teams (PWA) - Chat`,
     `0x2`), GNOME Weather (`class org.gnome.Weather`, `0x3`), two
     Alacritty (titles without "terminal"), and Google Chrome
     (`class google-chrome`, a title without "weather"). The unit tests
     import this list, so the Q6 bar has one fixture.
   - Read `config.LOG_FILE`. Keep lines whose tag is `heard` followed by a
     quoted string, and parse that string with `ast.literal_eval`. Print
     `ROUTE  <text> -> <description or "answer">` or `model  <text>`, then
     `N lines, M routed`.
   - `--live` makes the clients callable
     `lambda: Executor(Config())._query_rows("clients")`, a read-only
     `hyprctl -j clients`. It still only prints.
   → verify by step 9.

6. **`src/omarchy_voice/local_engine.py`: the hook**, against
   `feat/72-listen-faster`.
   - Add `router` to the `from . import …` line (`:35`).
   - Change the signature to `_answer(self, text: str, trace=None, *,
     routable: bool = True)` (`:376`), per detail B.
   - Add `async def _route(self, text)`. It returns `None` if the router is
     off (`not self.config.router`) or `self._held()` (`:173`) is not
     `None`. Otherwise it returns `await asyncio.to_thread(router.route,
     text, lambda: self.executor._query_rows("clients"),
     self.config.wake_word)`. An `Exception` is logged as `warn    router:
     <type>: <exc>` and returns `None` (detail E).
   - Add `async def _run_route(self, route, text)`:
     - For an answer: log `routed  window list`, `await
       self._say(route.answer)`, and note `window list → answered`.
     - For a call: `description = self.executor.describe(route.tool,
       route.args)`, log `routed  <description>`, then `result = await
       asyncio.to_thread(self.executor.call, route.tool, route.args)`.
       Choose the spoken line and the outcome word per decision 14 and
       detail A: `dry-run`, `ok`, `held for confirmation` (when
       `self.executor.pending == (route.tool, route.args)` after the call)
       or `failed: <first sentence>`. Speak it if there is one, and call
       `self.brain.note(f'User said "{text}" → {description} → {outcome}')`.
   - In `_answer`, inside the existing `try` (`:395`), before
     `async for sentence in self.brain.ask_stream(text)` (`:396`):
     `hit = await self._route(text) if routable else None`. `if hit: await
     self._run_route(hit, text)`, else the unchanged `async for`. Everything
     after it is untouched and shared: the `except` (`:410`),
     `end_turn()` (`:421`), the trace finish, the held announcement
     (`:430-434`) and `_settle()` (`:435`).
   - In `_local_confirm` (`:546`), change the replay to
     `self._spawn(self._answer(self._last_text, routable=False))`.
   - `_inject` (`:517-529`) is unchanged. It reaches the hook through
     `_answer`.
   → verify by step 8.

7. **`src/omarchy_voice/claude_backend.py`: `WarmBrain` hears what was
   routed**, against `fix/69-snapshot-per-turn`.
   - In `WarmBrain.__init__` (`:597`), set `self._notes: list[str] = []`.
   - Add `def note(self, line: str) -> None`, which appends. Its docstring
     says it is not `ClaudeBrain._note` (`:354`), which records refusals to
     the log. This one tells the model what ran without it.
   - In `_turn` (`:739`), after
     `text = await asyncio.to_thread(_with_desktop, text)` (`:742`) and
     before `self._dirty = True`: if `self._notes` is set, prepend
     `"# Done without you since your last turn (already run, do not
     repeat)\n\n" + "\n".join(f"- {n}" for n in notes) + "\n\n"`, then clear
     `self._notes`. The snapshot and "# What the user said" come after it,
     unchanged.
   → verify by step 8.

8. **Tests.**
   - **`tests/test_router.py` (new)** imports `FIXTURE_CLIENTS` from
     `tools/eval_router.py`, by adding `tools/` to `sys.path` the way the
     tests add `src/`. The clients callable is `lambda: (FIXTURE_CLIENTS,
     None)` unless a case says otherwise.
     - The 11 command lines, each to its call:
       "Move Discord back to workspace 3, please." →
       `hypr_dispatch window.move {workspace "3", follow False, window
       address:0x1}`, `said` "Moved Discord to workspace three.".
       "Open up a new terminal for me, please.", "Can you open up a new
       terminal?" and "Open up a new terminal, please." →
       `omarchy_cli {"command": "launch terminal"}`.
       "What windows do I have open?", "What windows are opened on this
       desktop?" and "What windows do I have open on this desktop?" →
       `tool None`, and the answer contains "Discord", "Microsoft Teams",
       "Weather" and "two Alacritty".
       "Can you bring up my Teams window?" → `focus {window address:0x2}`.
       "Can you switch to workspace one, please?" → `focus {workspace
       "1"}`, `said` "Workspace one.".
       "Okay, close the weather window, please." → `window.close {window
       address:0x3}`, `said` "Closed Weather.".
     - Every sentence in decision 13 that must return `None` returns `None`.
       "Bring up Teams." and "Close the weather window." are routed.
     - "close alacritty" (two windows) → `None` (detail F).
     - "Close the weather window" with `FIXTURE_CLIENTS` plus a Chrome
       window titled "Weather forecast - Google Chrome" → `None`
       (exactly-one rule, not top-score).
     - "What windows are open" with the clients returning `([], "hyprctl
       clients failed: …")` → `None`. With `([], None)`, the answer is
       "Nothing is open.".
     - "turn it down" with a counting clients callable → `None`, and the
       callable was called 0 times.
     - "Oma, switch to workspace two" with `wake="oma"` → `focus {workspace
       "2"}`.
   - **`tests/test_local_engine.py`** (the `EngineTestCase` harness on
     `feat/72-listen-faster:tests/test_local_engine.py:137-175`):
     - In `build()`, stub `session.executor._query_rows = lambda kind:
       (self.clients, None) if self.clients is not None else ([], "no
       hyprctl in tests")`, with `self.clients = None` in `asyncSetUp`.
       Without the stub, every existing test that says "close the browser"
       would run the real `hyprctl`, and whether it routed would depend on
       the machine. With the default error it never routes, so the existing
       tests keep their meaning.
     - `FakeBrain` gets `self.notes = []` and `def note(self, line)`.
     - New `RouterTests`, each with `self.clients = FIXTURE_CLIENTS`:
       (a) "switch to workspace one" via `_answer`: `brain.asked == []`,
       spoken `["[dry-run] would run: dispatch focus workspace='1'"]`, and one
       `brain.notes` entry ending `→ dry-run`.
       (b) With `dry_run=False` and `session.executor._tool_hypr_dispatch`
       faked to return `Result(True, "ok")`: spoken `["Workspace one."]`.
       The fake goes on the handler rather than `_shell`, because rendering
       needs the Hyprland Lua stub, which a test machine may not have.
       (c) A miss, "what is the weather for today?": `brain.asked ==
       ["what is the weather for today?"]`.
       (d) Held: set `session.brain.pending = "x"`, then say "switch to
       workspace one": it goes to the brain (`asked` has it).
       (e) `router=False`: the same line goes to the brain.
       (f) `confirm_patterns=[r"window\.close"]`, "close the weather
       window": `executor.pending` is set, the spoken text contains "needs
       confirming", it does not contain "Stop here", and the note ends
       `→ held for confirmation`.
       (g) `deny_patterns=[r"window\.close"]`: the spoken line starts
       with "refused", does not contain "Tell the user", and nothing is
       pending.
       (h) `_inject("what windows are open")`, awaited through the spawned
       task: the answer is spoken, and `brain.asked == []`.
       (i) The replay: `FakeBrain(hold="x")` on the text "switch to
       workspace one" with the router off for the first turn. Turn it on,
       then `_local_confirm()`. `brain.asked` has the text twice (detail B).
       (j) With `trace_timings=True`, a routed turn logs a `TIMING` line.
   - **`tests/test_claude_backend.py`** (`WarmBrainTests`,
     `fix/69-snapshot-per-turn:tests/test_claude_backend.py:632`, where
     `_with_desktop` is patched to identity): after `subject.note('User said
     "close weather" → dispatch window.close … → ok')`, the next
     `ask_stream("hi")` sends a query that starts with `"# Done without you
     since your last turn"`, contains the note line and ends with `"hi"`.
     The following `ask_stream("again")` sends exactly `"again"`. In
     `SnapshotPerTurnTests` (`:847`), with a note, the query starts with the
     notes heading, then `# The desktop right now`, and ends with the user's
     text under `# What the user said`.
   - **Existing tests pass unchanged:** `HoldTests` (`:334-384`),
     `OneBreathTests` (`:467-503`), `ControlTests` and all of
     `test_claude_backend.py`.
   - **Mutation checks**, each restored after:
     (1) In `_one_window`, accept a unique top score instead of
     `len(ranked) == 1` → the weather-tab case fails.
     (2) Drop the `self._held()` check in `_route` → test (d) fails.
     (3) Drop `routable=False` from the replay → test (i) fails.
   → verify: `nix develop -c pytest tests -q` equals the step-0 baseline plus
   the new tests, with no new failures.

9. **The replay, read only.**
   `nix develop -c python3 tools/eval_router.py` over the real `session.log`
   → the 11 Q6 lines print `ROUTE`. Every other `ROUTE` line is copied into
   the PR description and checked against decision 3 (detail G). Then
   `nix develop -c python3 tools/eval_router.py --live` → it prints what
   each line would resolve to on this desktop, and nothing runs. Record in
   the PR any line that resolves differently live, such as a PWA title that
   is now the only match.

10. **Whole check.** `nix flake check --no-write-lock-file` → it passes.

11. **No live daemon check.** Do not restart the service, speak to it or
    launch anything. Do not run `omarchy-voice say` for this: the router is
    not on that path (decision 5), and a dry run would only measure the
    model. The first real measure is the user's own use afterwards: `routed`
    lines and #81's `TIMING` lines in `session.log`, against the intent's
    9-14 s.

12. **Follow-ups and PR.** The spec names no unconditional follow-up. A
    shortlist issue (decision 2) and new-route issues (decision 3) are opened
    only when the replay shows the need, so none is opened now. Push, and
    open a PR that closes #71, links the intent, spec and plan, and states
    the landing order (after #85; which of #81/#78 it was rebased onto).

## Tests

```
nix develop -c pytest tests -q                                    # baseline + new, 0 new failures
nix develop -c pytest tests/test_router.py tests/test_local_engine.py tests/test_claude_backend.py -q
nix develop -c python3 tools/eval_router.py                       # real log, read only: the 11 Q6 lines ROUTE
nix develop -c python3 tools/eval_router.py --live                # real windows, read only, runs nothing
nix flake check --no-write-lock-file                              # CI parity
```

Plus the three mutation checks in step 8.

## Deviations made while implementing

- **Detail C: a raw class also gains a capital.** Discord's real class is
  `discord`, so the part after the last dot alone spoke "Moved discord to
  workspace three.", not the line step 8 expects. `_spoken` upper-cases the
  first letter of a raw class. A PWA title is unchanged.
- **Decision 11: stricter, in the same direction.** NAME is refused when any
  of its words is in `PRONOUNS`, not only when the whole NAME is one. So
  "close all windows" is refused before the window list is ranked, rather
  than depending on no title containing "all windows".
- **Normalising, trivial additions:** "ok" is dropped with "okay", and
  "dont" (no apostrophe) is refused with "don't". Curly apostrophes are
  straightened first.
- **Step 1 ran after #85, #81 and #78 had all merged** (main `9c8e45a`), so
  the merged path was taken everywhere and no "still open" difference applied.

- **Rebased onto main `fc52245` (#75, #84, #76, #73).** Conflicts in
  `LocalSession._answer` and `_local_confirm` were resolved by keeping #76's
  `release=` turn and its `_local_confirm` exactly; `WarmBrain.note` merged
  cleanly into `_turn`.
- **Detail B reshaped (2026-09-24).** The confirm replay no longer exists
  (#76), so `routable` is removed and the router is skipped whenever
  `release` is set. Test (i) became `test_a_release_turn_is_never_routed`;
  step 8 mutation (3) is now "remove the release skip → that test fails".
- **Decision 11 changed for close (2026-09-24, the approver's decision after
  review of PR #93).** `route("close notes", …)` with only a Chrome window
  titled "Release notes" closed Chrome, because a web page sets its window's
  title. `_one_window(…, by_class=True)` refuses a match below score 2.0,
  and only close passes it. Focus and move keep title-only matches. Tests:
  "close notes" (title only) → None, "close chrome" (class) → routed,
  "focus notes" (title only) → routed. Mutation: allow title-only on close →
  `test_close_needs_a_class_match` fails.

## Rollback

- **Without reverting:** set `router = false` in `config.toml` and restart
  the daemon. Every sentence goes to the model again, as before.
- **Revert:** it is a single squash-merged PR, with no Nix change, no
  schema change and no persisted state. `git revert <merge>` removes
  `router.py`, the hook, `WarmBrain.note` and the eval script. A user who
  set `router = …` in `config.toml` keeps a harmless line: `load()`
  collects it into `unknown_keys` (`config.py:499-502` on
  `feat/72-listen-faster`) and does not fail. Notes queued in a running
  daemon go away when it restarts.
