---
status: approved
issue: 71
intent: intent/2026-09-24-71-answer-without-the-model.md
---

# Spec: a common command must not wait for the model

## The intent's open questions, answered

The intent was approved without answers to its six questions, so each is
decided here with the reasoning shown. Q6 was treated as a claim to be tested
and was measured with a prototype. Any of these decisions can be rejected at
this gate.

### Q6 first, because it was measured: yes, the eval gates the change. The bar is 11 of 11 routed and 0 of the rest

A scratch prototype (not repo code, about 70 lines, standard library plus the
repo's own `_rank_windows`) implements the grammar in Design §1. It was
replayed over every line of `~/.local/state/omarchy-voice/session.log`
containing `heard` (read only). That is 114 lines: the intent's 111, plus
three wake lines, two of which only contain the word "heard". The open
windows came from a fake client list: Discord, a Teams PWA with a hashed
Chrome class (the class of the Teams window open on this machine now, read
with `hyprctl clients -j`), GNOME Weather, two Alacritty terminals, and
Google Chrome. Nothing was dispatched.

```
114 lines, 11 routed, 9 us/line
ROUTE  'Move Discord back to workspace 3, please.'      -> hypr_dispatch window.move {workspace 3, window address:0x1}
ROUTE  'Open up a new terminal for me, please.'         -> omarchy_cli launch terminal
ROUTE  'What windows do I have open?'  (x2)             -> answer from the client list
ROUTE  'Can you bring up my Teams window?'              -> hypr_dispatch focus {window address:0x2}
ROUTE  'Can you switch to workspace one, please?'       -> hypr_dispatch focus {workspace 1}
ROUTE  'Okay, close the weather window, please.'        -> hypr_dispatch window.close {window address:0x3}
ROUTE  'What windows are opened on this desktop?'       -> answer from the client list
ROUTE  'Can you open up a new terminal?'                -> omarchy_cli launch terminal
ROUTE  'What windows do I have open on this desktop?'   -> answer from the client list
ROUTE  'Open up a new terminal, please.'                -> omarchy_cli launch terminal
```

These are exactly the intent's 11 fixed commands. None of the other 103 lines
was routed. That includes the ones containing command words: "Plus
terminal.", "I will turn the light.", "Opened it.", "Perfect. Close it.",
"Oh, that's fine. Close the window.", "Go back to works, bit one.", "Can you
show me today's weather please?", "Can you open up today's weather?" and
"That's fine. You can stop it. You can close the terminal. This was a test."

The same prototype, run on sentences written to break it:

| Sentence | Result | Why |
|---|---|---|
| Don't close it. / Do not switch to workspace two. | model | negation |
| Turn it down. | model | no route (volume is not in this change, Q2) |
| Close it. / Close the window. / Move it to workspace 2. | model | the target is a pronoun or the last turn |
| Close the terminal. | model | two terminals are open |
| Focus the terminal. | model | no window's class or title contains "terminal" |
| Close chrome. | model | "chrome" is in several windows' classes |
| Close all windows. | model | not one window |
| Switch to workspace 3 and open firefox. / Close Discord and Teams. | model | compound |
| Close weather, no, discord. | model | negation / correction |
| What is the weather for today? / Can you open up today's weather? | model | no route. "Open <app>" is not routed (see Q2), and this is the weather false match the intent found |
| Bring up Teams. / Close the weather window. | **routed** | exactly one open window matches |

So the gate, enforced in tests and in the replay script (Verification):
**all 11 fixed commands routed against the fixture windows, and zero routes on
every other logged line and on every sentence in the table above.** A later
change to the grammar has to keep both numbers.

### Q1: in front of the model, exact hits only (option a). No shortlist for near misses yet

A hit runs through the `Executor` and is spoken. Anything else goes to Claude
exactly as it does today. The warm session is told what ran, at the start of
its next turn (Design §3), so a later "close it" can refer to it.

Option (b), a shortlist prepended to the prompt, still costs a model return for
every command. Once #84 stops `ToolSearch` being needed, and #85 lets
`launch_app` take a plain name, the model already gets from a name to a call
in one step. A shortlist would save it a lookup it no longer makes, and it
would add text to every turn. So (c) collapses to (a) for now. The replay
script shows the near misses, and if they turn out to be common, a
shortlist becomes a follow-up issue.

### Q2: only what was measured. That means workspace N, open terminal, list windows, and focus/move/close one named open window

Volume, brightness, media, lock, screenshot, nightlight and theme do not appear
once in 111 lines, and each brings a risk the measured four do not. Two
examples: "turn it down" against "turn it up" (direction), and nightlight or
mute (a toggle claimed as "off"). Shipping them would be building for a guess.
The replay script is the yardstick. When one of them shows up in the log, it
becomes one row of the grammar and one test in a new issue, with the
direction and toggle rules the intent sets.

"Open <app>" by name is **not** routed either, although #85's `clear_match`
could resolve it. The intent showed why: "today's weather" clear-matches GNOME
Weather while the user wanted a forecast. A clear app is not a clear intent.
After #85, "open zed" is one `launch_app("zed")` model call, which is good
enough. Only "open a terminal" is routed. It names a fixed Omarchy route
(`omarchy launch terminal`, which the realtime engine sent at 11:28:32 and
20:09:31), not an app lookup.

### Q3: no learned cache, not now and not as a planned follow-up

It would learn from one person's four days, and the log shows what it would
learn. The realtime model's "close the weather window" became
`hl.dsp.window.close()` with **no target** (log 12:01:22), and that was
counted as a success. A cache replays whatever the model guessed, as if it
were a rule. "Verified" has no definition that would have caught that case.
The replay script covers the same need, which is to see what the phrase table
misses, without anything acting on its own.

### Q4: the local engine only, including its typed `listen say` path

The router is called from `LocalSession._answer`
(`src/omarchy_voice/local_engine.py:339`). Spoken turns and `omarchy-voice
listen say` (`_inject`, `local_engine.py:480-492`) both go through it, so
both get it without extra code.

- **Not the realtime engine.** It already did these in 0-2 s (intent), and
  #77 is about the three engines' duplicated machinery. Adding a fourth copy
  of the router is the wrong direction.
- **Not the one-shot `omarchy-voice say`** (`cli.py:115`, a cold brain each
  call). It is the harness that #84's and #70's measurements use to time the
  model. Routing there would take the model out of its own benchmark.
- **Not MCP.** The caller brought its own model (intent).

### Q5: speak the result only, one short line

The persona's announce-then-report rule (`local_engine.py:69-86`) exists to
fill several seconds of silence while the model and then a tool run
(`:60-63`). A routed command reaches its tool in milliseconds, so an
announcement would be heard *after* the action. So only the outcome is
spoken: "Workspace one.", "Opening a terminal.", "Teams.", "Moved Discord to
workspace three.", "Closed Weather.", or the window list. A failure speaks the
`Result`'s own error. A hold speaks the existing "… needs confirming" line
(`local_engine.py:389-397`). `LOCAL_PERSONA` is unchanged, because it still
governs every turn the model takes.

## Design

The change is three pieces: a pure matcher in a new module, about 20 lines in
`_answer`, and a note queue on `WarmBrain`. `tools.py` and the policy are not
changed.

### 1. `src/omarchy_voice/router.py`: words to one call, or `None`

`route(text, clients) -> Route | None`. `Route` is a small dataclass with
`tool`, `args`, `said` (the line to speak on success) and `answer` (set only
for the window list, which runs nothing). `clients` is a zero-argument
callable, so the hyprctl query only runs for a line that has already matched
a pattern that needs one (the non-command lines never pay for it).

Normalising, in order:

1. Lowercase, and strip the surrounding quote marks.
2. **Exactly one sentence.** The text is split on `.?!`. More than one
   non-empty piece means `None`. "Perfect. Close it." goes to the model.
3. Commas are turned into spaces. Politeness is removed: "can/could/would/will
   you", "please", "for me", "just". Leading "okay", "so", "right", "now",
   the wake word and "hey <wake word>" are also removed.

Refusals, checked before any pattern:

- **Negation and compounds:** a word from `don't, do not, not, never, no,
  stop, cancel, undo, without, except, and, then, but` means `None`. This is
  deliberately broad. A command that says "and" is two commands, and two is
  the model's job.

Patterns, each a `re.fullmatch` over the whole normalised sentence (never a
search), tried in this order:

| Kind | Pattern (sketch) | Call |
|---|---|---|
| workspace | `[(switch\|go\|change) [back] to] workspace N` | `hypr_dispatch {dispatcher: "focus", args: {workspace: "N"}}` |
| terminal | `open [up] [a] [new] terminal` | `omarchy_cli {command: "launch terminal"}` |
| list | `what windows (do i have\|are\|have i got) open[ed] [on (this\|my\|the) desktop]` | none: answered from the client list |
| move | `move [the\|my] NAME [window\|app] [back] to workspace N` | `hypr_dispatch {dispatcher: "window.move", args: {workspace: "N", follow: false, window: "address:…"}}` |
| close | `close [the\|my] NAME [window\|app]` | `hypr_dispatch {dispatcher: "window.close", args: {window: "address:…"}}` |
| focus | `(bring up\|focus\|switch to\|go to) [the\|my] NAME [window\|app]` | `hypr_dispatch {dispatcher: "focus", args: {window: "address:…"}}` |

`N` is 1-10, as digits or words. `follow: false` is used because "move
Discord" does not say "and take me there". The realtime model's `follow =
true` (log 11:28:01) was its guess, not the user's words.

**NAME resolves to exactly one open window, or nothing.** It reuses
`_rank_windows` (`tools.py:546`), the ranking `_resolve_window` uses
(`tools.py:2792`), so "what a name means" is still defined once. The rule is
stricter than `_resolve_window`, which accepts a unique *top* score
(`tools.py:2846-2858`). The router requires that **only one window matches at
any score**. "Close the weather window" with a browser tab titled "…weather…"
also open goes to the model. NAME must not be a pronoun or a quantity: `it,
this, that, them, this one, that one, everything, all, window, windows`.
A title-only match is allowed, because Omarchy PWAs have hashed classes, and
Teams is only findable by its title (checked on this machine). That is safe
because nothing else may match at all.

If the client query fails, `_query_rows` (`tools.py:2972`) returns an error
and the router returns `None`. It does not use `_query_json` (`tools.py:3002`),
because that function's docstring says it is unsafe wherever emptiness is read
as a fact. "What windows are open" is exactly that kind of caller. That is
the #75 bug, and it is not repeated here.

The window list is spoken as names from `_short_class` (`tools.py:533`),
with duplicates counted, e.g. "Open: Discord, Microsoft Teams (PWA), Weather,
and two Alacritty." A window list is what the snapshot already puts in the
prompt, so reading it involves no gate.

No toggle and no direction route exists in this change. The module docstring
states the rule for adding one: a toggle's spoken line says "toggled", never
"on" or "off", and a direction word is part of the pattern, never a wildcard.

### 2. `LocalSession._answer`: try the router first

In `_answer` (`local_engine.py:339`), after `heard` is logged and the trace is
started (`:349-357`), and before `self.brain.ask_stream` (`:359`):

- The router is skipped when `self._held()` (`:171`) is not `None`. While
  something waits for a yes, every word goes to the existing confirm path.
- The router is also skipped when `config.router` is false. This is one new
  `bool = True` in `Config` (`config.py:207`), documented in
  `share/config.example.toml`. It exists so a misfire can be switched off
  without a rebuild.
- `route = router.route(text, lambda: self.executor._query_rows("clients"))`.
  On `None`, `_answer` continues exactly as today.
- On a hit with an `answer`, it is spoken. On a hit with a call,
  `await asyncio.to_thread(self.executor.call, route.tool, route.args)` runs
  it, as `_local_confirm` already does for `run_pending` (`:494-500`). A hit
  logs `routed  <description>`. `Executor.call` (`tools.py:1622`) →
  `_call_locked` (`:1637`) → `policy.check(description)` (`:1643`) applies
  unchanged: a deny is refused and spoken, and a confirm rule parks the call
  in `executor.pending` (`:1654`), which the existing post-turn code at
  `:389-397` announces. The `on_action` hook logs the `action` line as it
  does for a model call. None of the six routes matches `DEFAULT_CONFIRM` or
  `DEFAULT_DENY` (`config.py:108`, `:132`), so only a user's own rules could
  hold or refuse them, and those rules still work.
- The spoken line is `route.said` if `result.ok`. In dry-run it is the
  `[dry-run] would run: …` output. Otherwise it is `result.output`.
- Then the same tail as a model turn: `executor.end_turn()`, closing the trace
  span, the held check, and `_settle()`. The router shares this tail, and
  does not duplicate it.

The router works when Claude is down (`NO_SESSION`), because it never touches
the brain.

### 3. `WarmBrain` hears what was routed

`WarmBrain` (`claude_backend.py:567`) gains `note(line: str)`, which appends
to `self._notes`. `_turn` (`:723`) puts the notes in front of the text it
sends, then clears them:

```
# Done without you since your last turn (already run, do not repeat)
- User said "close the weather window" → window.close address:0x3 → ok
```

This costs nothing: there is no extra model turn, only a few lines on the
next one. If #78 (#69, `_with_desktop`) merges first, the notes go in front of
its snapshot block, and the user's text stays under "What the user said". A
held route's note says "held for confirmation", so a spoken "confirm" on the
next model turn has something to confirm. If the warm session is rebuilt
(`reset_turn`, `:665`), the notes are lost. That is the same as the rest of
that session's memory, and nothing to add for.

### 4. The eval: `tools/eval_router.py`

This is a replay script in the style of `tools/verify_matching.py`. It reads
`session.log` (read only), takes every `heard` line, and prints `ROUTE` or
`model` per line and a total. It uses a built-in fixture client list, or
`hyprctl clients -j` with `--live`, which is a read-only query. It imports
`router.route` and never calls the `Executor`, so it cannot act. It is how a
grammar change is judged (Q6), and it is how a new route (Q2) earns its row.

The log itself is **not** committed. It holds names of people and other
private lines. The unit tests carry the 11 command lines and the
command-word non-commands quoted in Q6, all free of personal data.

### 5. Order against open PRs

- **#85 (`feat/70-find-what-is-installed`) must merge before this plan
  lands.** The router leaves "open <app>" to the model on purpose (Q2), and
  that is only acceptable once `launch_app` resolves a plain name in one call
  (`tools.py:2087-2088` on that branch). No app matching is added here, and
  `find_apps`/`clear_match` are not duplicated.
- **#81 (`feat/72-listen-faster`)** changes `_answer` to `_answer(self, text,
  trace=None)` and adds `_hear`. The router hook is a block inside `_answer`
  that uses no names #81 adds (`router`, `route`, `note`), so a rebase is a
  small textual merge. Whichever merges first, the trace #81 starts at the end
  of speech covers the routed call, because the `Executor` already marks
  `TOOL` spans (`tools.py:1622-1635`).
- **#84 (`perf/84-tools-not-deferred`)** removes `ToolSearch` round trips of
  1.1-3.4 s. After it, a model-handled command still costs at least two
  model returns of 1.5-7.6 s each (intent). The router removes those as well,
  so its gain does not depend on #84 and is not swallowed by it.
- **#78 (`fix/69-snapshot-per-turn`)**: see §3.

## Alternatives rejected

- **A shortlist for near misses (options b and c).** It costs a model return
  anyway, and after #84 and #85 it would save little. It is deferred until the
  replay shows near misses are common (Q1).
- **Routing "open <app>" through `clear_match`.** It resolves apps, not
  intents: "today's weather" clear-matches GNOME Weather (intent, Q2).
- **A learned transcript → tool-calls cache.** It would replay the model's
  guesses, such as the untargeted close at 12:01:22, as if they were rules
  (Q3).
- **Volume, media, lock, screenshot, theme and nightlight now.** They do not
  appear in the log, and they carry direction and toggle risk (Q2).
- **Using Omarchy's binds or `omarchy commands --json` as the vocabulary.**
  The binds' `arg` is an opaque Lua reference, not something that can be
  run. The 365 routes are a list of what exists, not of what is said. Neither
  is needed for the four measured commands.
- **`rapidfuzz` or Model2Vec.** The prototype routes 11 of 11 and misfires on
  0, with `re` at 9 µs a line. Fuzziness is what the intent forbids acting on.
- **Using `_resolve_window` as the router's resolver.** It picks a unique top
  score over lower matches, which is right for a model that asked for a
  window. For a router acting without the model, that is a guess, so the
  router uses the stricter "exactly one match".
- **The router inside `Executor` or `WarmBrain.ask_stream`.** `Executor` is
  shared with MCP and realtime, which must not get it (Q4). `ask_stream`
  cannot speak through the session's mouth or check the held state without
  taking on the session's job.
- **Dropping the persona's announcement.** It still covers the model turns,
  which are every turn the router does not take.

## Risks

- **A misfire acts on the wrong window.** Mitigations: a whole-sentence
  match, exactly one sentence, no negation or compound words, no pronouns, and
  exactly one matching window. The call still goes through the gate. The
  remaining case is a window whose title happens to contain a name the user
  said about a different, closed app. That is a focus or close of one real
  window, the same thing the model would do from the same words. The
  `router = false` switch turns it off. Host: any machine running the
  local engine.
- **The model does not know what happened.** The note (§3) covers the next
  turn. If the session is rebuilt between turns, the note is gone, and so is
  the rest of that session's memory.
- **Title-only matches for PWAs.** A retitled page, such as a Teams chat
  named "Weather", could be the only match for "weather". This is the price
  of Teams working at all. The replay script's `--live` mode shows what
  would match on the real desktop.
- **Merge conflicts with #81 and #78** in `_answer` and `WarmBrain._turn`.
  They are small and expected (§5).
- **Grammar drift.** Every added pattern is another way to misfire. The Q6
  bar is enforced in tests, so a looser pattern fails CI, not the user.
- No Nix or packaging change: the new module is plain Python in the existing
  package.

## Verification

- **Unit (`pytest tests -q` in `nix develop`)**, with fake clients and a fake
  `Executor`. Nothing touches Hyprland.
  - `tests/test_router.py`: the 11 command lines route to the calls in the Q6
    output against the fixture windows. Every sentence in the Q6 table that
    says "model" returns `None`, and so do the quoted command-word
    non-commands. "Close the terminal" with two terminals returns `None`. A
    failed client query (`_query_rows` returning an error) returns `None` for
    "what windows are open", never "nothing is open". The `clients` callable
    is not called for a line that matches no pattern.
  - `tests/test_local_engine.py`: a routed line calls `executor.call` once and
    never calls `brain.ask_stream`. A miss calls `ask_stream` with the
    unchanged text. With something held, a line that would route goes to the
    brain instead. With `router = false`, nothing routes. A route that the
    fake policy holds leaves `executor.pending` set, and speaks the existing
    "needs confirming" line. A route that policy denies speaks the refusal.
  - `tests/test_claude_backend.py`: after `note(...)`, the next `_turn`
    query starts with the notes block, and the one after it does not.
- **`nix flake check --no-write-lock-file`** passes.
- **Live, read-only:**
  - `python tools/eval_router.py` over the real `session.log` prints 11
    `ROUTE` lines (the Q6 list) and `model` for every other line.
  - `python tools/eval_router.py --live` resolves names against `hyprctl
    clients -j` and only prints. It runs nothing.
  - No daemon restart and no live utterance is part of this check. The first
    real measure is the user's own use afterwards: `routed` lines and #81's
    trace in `session.log`, compared with the intent's 9-14 s.
