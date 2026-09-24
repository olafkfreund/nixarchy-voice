---
status: draft
issue: 121
intent: intent/2026-09-24-121-realtime-engine-future.md
---

# Spec: remove the OpenAI realtime engine, and say so everywhere

Line numbers are from `origin/main` at `2172c4f` (v1.0.0). That commit only
bumps version strings over the intent's `6a9a3f5`, so the intent's line
numbers still hold. Issue #121 was confirmed OPEN with `gh issue view 121` on
2026-09-24.

## Correction to the intent

The intent says the suite has "950 tests". Today it has **1,119**. Counted on
`2172c4f` with `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`:

- `nix develop -c pytest tests -q` gives `1119 passed, 1 warning, 825 subtests passed`.
- `nix develop -c python3 -m unittest discover -s tests` gives `Ran 1119 tests ... OK`.
- `pytest --collect-only -q`, grouped by file, gives `tests/test_realtime.py`
  96 and `tests/test_realtime_wire.py` 2.

So realtime's own tests are 98 of 1,119, not 98 of 950.

## Decisions on the intent's open questions

The owner approved the intent with a bare "approve all", so this spec decides
each question. Each decision can be rejected at this gate.

### 1. Keep, freeze or remove? **Remove.**

The evidence, weighed both ways:

For keeping it:

- **It is faster.** README:390-394 measures the local engine at 1.5-7.6 s
  median to the first spoken sentence (warm), against realtime's roughly
  1-2 s. The owner wants faster responses, and removing realtime gives up the
  fastest path this repo has. This spec says that plainly.
- It hears audio, not a transcript (config.py:397-400).

For removing it:

- **Nobody uses it.** `~/.local/state/omarchy-voice/session.log` has 21
  `engine=realtime` starts, the last at 2026-09-12 16:26:58 (log line 2396).
  Since then there have been 37 `engine=local` starts, the latest on
  2026-09-22 23:47:09. The same log has 22 `session_expired` errors: the
  60-minute cap ended sessions more often than the owner started them.
- **No host sets it.** p620's config
  (`~/.config/nixos/hosts/p620/nixos/nixarchy.nix`) never sets `engine`.
  Neither does razer's (see decision 4). Both hosts run local.
- **Its main advantage is switched off on both hosts.** Native barge-in only
  runs with `ears.barge_in = true` (realtime.py:770, :983). Both hosts set it
  to `false`: p620 at nixarchy.nix:215, razer at nixarchy.nix:190. Both give
  the same reason: on speakers her voice comes back in as a user turn. So the
  fleet never used native barge-in.
- **It is already degraded.** Since #113 (15a2985), `confirm_last` always
  refuses on realtime (realtime.py:265-282). Only the key releases a held
  action. Spoken confirmation works only on the local engine (#86).
- **It doubles every safety change.** #86 was done twice, because the
  realtime half became #113. Its only commits since 2026-09-20 are #74 (moving
  the watch text out so the local engine could share it) and #113. Both came
  from local-engine work. That is the cost the owner asked to cut: "less to
  maintain".
- **It works against desktop integration.** It has no wake word
  (README:79-81), streams room audio to OpenAI the whole time the mic is open,
  and bills that audio by the second.
- **Speed work already targets the local chain.** #79 (the speaking side) and
  #80 (end-of-turn detection) aim at the local engine's latency. Keeping
  realtime does not make the engine in use any faster.

Freezing was rejected (see Alternatives). It keeps the cost and adds a rule
that has to be judged case by case (question 2).

### 2. If frozen, what counts as a safety fix? **Not applicable.**

With the engine removed, no future fix needs a realtime half. After this
change, #77, #114 and every later consent or turn-taking issue can say
"realtime: removed in #121" and stop there.

### 3. What happens to an existing `engine = "openai"`? **A clear refusal. No fallback.**

`cmd_run` (cli.py:188-201) will not start anything when
`realtime_engine == "openai"`. It uses the same "not set up" path the local
engine already uses for a missing dependency (local_engine.py:924-937):

- It prints `the OpenAI realtime engine was removed in 2.0.0 (#121). Delete
  engine = "openai" under [realtime], or set it to "local". The last release
  with it is v1.0.0.`
- It sets the bar state to `unconfigured` with that sentence.
- It sends one notification: `voice has no backend: run omarchy-voice doctor`.
- It exits 0.

Exit 0 follows that path's precedent (local_engine.py:935-936): under
`Restart=on-failure` (nix/hm-module.nix:305), a non-zero exit would restart
three times inside `StartLimitIntervalSec = 60` and then fail. That looks the
same as a crash. The refusal is not silent, because the bar, the notification,
the journal and `doctor` all say it.

Falling back to local was rejected. A user who picked realtime to keep audio
off Anthropic, or to use OpenAI's voice, would get a different vendor and a
different voice without being asked. That is what the intent's constraint
rules out.

Any other value that is not `"local"` (for example the typo `"locl"`) still
runs local, as `WiringTests.test_anything_unrecognised_runs_the_local_chain`
(tests/test_local_engine.py:1563) pins today. There is only one engine left,
so a typo cannot end up somewhere worse. `doctor` flags any value other than
`"local"` or `""`.

### 4. Does the answer depend on razer? **No. razer was checked: local.**

I read `~/.config/nixos/hosts/razer/` read-only. `programs.omarchy-voice`
(razer `nixos/nixarchy.nix:171-202`) sets `apiKeyFile`, `mouth.speak`,
`ears.barge_in = false`, `hands.allow_shell` and `keybinding`. It does not set
`engine`. A `grep -rn engine` across the host directory finds nothing for the
voice module. razer runs local. Its comments at :165 and :181 still describe
"the OpenAI Realtime API" (p620's do too, at :183 and :205). Those are
comments in the NixOS config repo, not in this one. Fixing them is a follow-up
for the owner, because this task treats both configs as read-only.

### 5. Is audio-native hearing or native barge-in on the roadmap? **Not in this repo.**

- Native barge-in is covered in decision 1: both hosts turn it off because of
  echo. The local engine's `barge_in` (local_engine.py:253-261) and #114 are
  where interruption work happens now.
- Audio-native hearing (tone, hesitation) is a real loss, and this spec says
  so in the README (see Design, README). Removing the engine does not rule
  it out. `realtime.py` stays in git history and in the v1.0.0 tag. If it
  comes back, it comes back as a new issue built on the local engine's consent
  model (#86). Reviving a module that has a separate gate was rejected
  (#113).

## Design

One PR. It removes the engine, moves the helpers that outlive it, makes the
refusal explicit, and updates doctor, config, README, the HM module and the
dependencies to match. The version becomes 2.0.0, because a supported config
value stops working.

### What is deleted

| Path | Lines | Why |
| --- | --- | --- |
| `src/omarchy_voice/realtime.py` | 1,708 | the engine |
| `tests/test_realtime.py` | 1,197 (96 tests) | apart from the 7 moved below |
| `tests/test_realtime_wire.py` | 171 (2 tests) | the websocket wire test |
| `tools/bench_realtime.py` | 148 | benchmarks realtime models only |

`websockets` is used only by `realtime.py` and that wire test. `cli.py:464`
only prints its name. It comes out of `pyproject.toml:12`,
`nix/package.nix:57` and both `withPackages` lists in `flake.nix:61` and
`:121`.

### The five helpers `local_engine.py:43` imports, plus the three doctor uses

These survive. Each moves to the only module that still uses it. They move
unchanged: the bodies are the same, and only the import changes.

| Helper | From | To | Users after the move |
| --- | --- | --- | --- |
| `ECHO_TAIL_SECONDS` | realtime.py:52 | `local_engine.py` | local_engine |
| `WATCH_POLL_SECONDS` | realtime.py:142 | `local_engine.py` | local_engine |
| `watch_headline` | realtime.py:148 | `local_engine.py` | local_engine, tests |
| `watch_message` | realtime.py:157 | `local_engine.py` | local_engine, test_terminal |
| `_run_until_done` | realtime.py:1678 | `local_engine.py` | local_engine.run |
| `default_source`, `_pactl`, `default_sink` | realtime.py:1551-1574 | `listen_local.py` | doctor (cli.py:504-506) |
| `echo_risk` | realtime.py:1576 | `listen_local.py` | doctor (cli.py:508) |

`listen_local.py` is chosen because it already owns the microphone
(`pw-record`, `check_ready`), and cli.py already imports it (cli.py:16).
`_run_until_done`'s annotation changes from `RealtimeSession` to "anything
with `async run() -> int`". Its body stays the same.

After this change nothing in `src/` imports `realtime`, and the local engine
no longer loads a websocket module in order to run.

### `cmd_run` (cli.py:188-201)

The `realtime_mod` import (cli.py:16) goes. `cmd_run` handles
`realtime_engine == "openai"` as in decision 3, and hands everything else to
`local_engine.run`. The config field `realtime_engine` (config.py:407) stays,
so that the value can be read and refused.

### Config (config.py)

- These fields are realtime-only (checked with grep: nothing else reads
  them), so they are removed: `realtime_model`, `realtime_voice`,
  `realtime_turn_detection`, `realtime_sample_rate`,
  `realtime_transcribe_model` (config.py:408-424), `history_items`,
  `silence_gate` and `silence_hold_seconds`.
- `idle_stop_seconds` stays, because the local engine reads it.
- Each removed key goes into `RETIRED_KEYS` (config.py:185) under the name the
  loader gives it (`realtime_voice`, `silence_gate`, and so on). Its
  explanation is "the OpenAI realtime engine was removed in 2.0.0 (#121)". An
  existing `[realtime] voice = "marin"` is then reported as retired, not as a
  typo. doctor already prints retired keys (cli.py:539).
- The comment block at config.py:387-406 is cut down to what is still true:
  `engine` exists only to refuse `"openai"`.
- `SAFETY_ID_FILE` (config.py:52) was only written by realtime's
  `_safety_identifier`, so it goes. An existing file on disk is left alone.

### doctor (cli.py:359-520)

After the change, doctor says:

- **openai**: the key line and `planner model ... (omarchy-voice say)` stay,
  because `say`'s planner fallback still uses the key. The
  `realtime model ..., voice ...` line (:367) and the safety-id line
  (:373-374) go.
- **engine**: always the local block (:432-445). If `realtime_engine` is
  `"openai"`, it adds `✗ engine = "openai": the OpenAI realtime engine was
  removed in 2.0.0 (#121). Set it to "local" or delete it.` Any other value
  that is not `"local"` or `""` gets `✗ engine = <value> is not an engine;
  running local`. The else-branch at :445-450 goes.
- **ears**: today, on a local-engine machine, this section prints
  `OpenAI Realtime (speech to speech)` and `room audio is streamed to OpenAI`
  (:465-469). It checks for `websockets` and the API key through
  `realtime_mod.check_ready` (:459-464). Those lines are false on every host
  in the fleet. They go, together with the silence-gate lines (:470-476) and
  the `transcribe_model` line (:484-486). The wake-word, `ask`, input/output
  and `barge_in` lines stay, and now read from `listen_local`.

### README

- "How it works" (README:52-89) keeps the local diagram, drops the
  `engine = "openai"` diagram and the paragraph under it, and says
  `omarchy-voice say` is the typed equivalent.
- "Speech without OpenAI" (README:381-433) becomes the description of the only
  engine. It keeps the measured latencies, including the plain sentence that
  local is slower than realtime's 1-2 s. That is now the target for #79 and
  #80, not an option to switch to. The paragraph "The OpenAI engine is not
  going anywhere" (:429-432) becomes: "The OpenAI realtime engine was removed
  in 2.0.0 (#121): unused since 2026-09-12, and every consent fix had to be
  written for it twice. v1.0.0 is the last release with it." The claim at
  :419 that turn-taking uses `[ears] silence_hold_seconds` is wrong today (the
  local engine never reads that key), and it goes as well.
- "Speed" (README:653-677) and "What it costs" (README:679-735) describe
  realtime token-per-minute limits and audio billing. They are cut to what
  applies to `say`'s chat planner and the local chain.
- "Speakers, and her hearing herself" (README:737+) and "Credit and what this
  fork changed" keep their history, reworded where they describe realtime as
  current.
- After the change, every remaining `realtime` or `OpenAI Realtime` mention
  in README is historical: either the removal note or the fork history.
  `docs/omarchy-voice.html` (3 mentions) gets the same treatment.

### Home Manager module

`nix/hm-module.nix:40`'s example `realtime.voice = "marin"` becomes
`ears.wake_word = "oma"`. `apiKeyFile` and `apiKeyEnv` (:62-95) stay unchanged,
because `say` needs the key. No option is added or removed, so p620 and
razer evaluate unchanged.

### Comments that call realtime current

`persona.py:3`, `mcp_server.py:52-55`, `session.py:4`, `feedback.py:79` and
:130, `listen_local.py:33` and :216, and the `local_engine.py` comments that
contrast the two engines (:11, :53-68, :160, :573, :614, :919). Each is
reworded where it would now be false. They are not deleted where they record
a measured reason. #77 depends on `LOCAL_PERSONA`'s comment (:53-68) no
longer pointing at `REALTIME_PERSONA`.

### Out of scope

- The `say` planner and its `gpt-4.1` default (config.py:264). The intent
  keeps it out, and #77's spec files it separately.
- p620's and razer's comments in the NixOS config repo (decision 4).

## Alternatives rejected

- **Keep it, fully supported.** Every consent and turn-taking fix stays
  double work, for an engine that has not started since 2026-09-12. Speed is
  its only strong argument, and the owner already chose local over it for
  12 days.
- **Freeze it (safety fixes only).** Most of the cost stays: 1,708 lines,
  98 tests, a websocket dependency, and a doctor that describes it. It also
  needs a judgement call on every fix (question 2). #113 shows that a frozen
  engine still gets consent work, and each such fix makes it worse to use
  (spoken confirm is gone).
- **Remove it, and fall back silently to local on `engine = "openai"`.** This
  changes the vendor and the voice without telling the user. The intent's
  constraint rules it out.
- **Remove it, and exit non-zero on `engine = "openai"`.** systemd restarts
  it three times, then marks it failed (hm-module.nix:269-270, :305), which
  looks the same as a crash. The existing "not set up" path exits 0 for this
  reason.
- **Leave the helpers in a stub `realtime.py`.** That keeps a module named
  after an engine that no longer exists, only so that one import line does
  not change.
- **A new `audio.py` for the PipeWire helpers.** It would be a new module for
  three small functions, when `listen_local.py` already owns the microphone.

## Risks

- **A host with `engine = "openai"` that I have not seen.** In this fleet I
  checked p620 and razer. A host outside the fleet gets the refusal in
  decision 3, not a silent change. The daemon stays down until the config is
  edited. That is intended.
- **Existing `[realtime]` keys.** A config with `voice`, `model` and so on
  loads without error. doctor lists those keys as retired. HM `settings` is
  freeform TOML, so evaluation does not break on any host.
- **A helper move changes behaviour.** The moves are verbatim. The moved tests
  (below) run against the new location, and `local_engine` tests that patch
  `local_engine._run_until_done` (tests/test_local_engine.py:1671) already
  patch that name.
- **Losing websockets breaks something else.** A grep finds `websockets` only
  in `realtime.py`, `cli.py:464` (a string) and `test_realtime_wire.py`. `mcp`
  and `claude-agent-sdk` bring in their own dependencies through nixpkgs.
  `nix flake check` proves the build.
- **Open branches touching realtime.** #114's spec says "the realtime engine
  is not touched", and its branch diff has no `realtime.py` changes. #110 is
  docs-only so far. #77 is rescoped by this spec.
- **Speed.** Users lose the 1-2 s path. This is stated in the README, not
  hidden.

## Verification

### Tests that fail on `main` first

Each one is written and run against `2172c4f` before the implementation
commit, and fails there:

1. `WiringTests.test_openai_is_refused_not_run`: `cmd_run(None,
   Config(realtime_engine="openai"))` returns 0, calls neither
   `local_engine.run` nor anything else that starts a session, sets bar state
   `unconfigured` with a message containing `removed` and `v1.0.0`, and
   notifies once. On main it calls `realtime_mod.run`. This replaces
   `test_openai_is_still_reachable` (tests/test_local_engine.py:1557) and
   `test_the_realtime_engine_says_the_same_thing` (:1661).
2. `test_the_realtime_module_is_gone`: importing `omarchy_voice.cli` and
   `omarchy_voice.local_engine` leaves `omarchy_voice.realtime` out of
   `sys.modules`, and `importlib.util.find_spec("omarchy_voice.realtime")` is
   `None`. On main, local_engine.py:43 imports it.
3. `ConfigLoadTests.test_realtime_keys_are_retired_not_typos`: a config with
   `[realtime] voice = "marin"`, `model = "x"` and `[ears] silence_gate = true`
   loads with `unknown_keys == []`, and all three appear in `retired_keys`. On
   main they are live fields, so `retired_keys` is empty.
4. `DoctorTests.test_local_doctor_never_mentions_streaming_to_openai`: on a
   default `Config()` with the helpers stubbed, doctor output contains neither
   `OpenAI Realtime` nor `streamed to OpenAI`. On main, cli.py:465-467 prints
   both.
5. `DoctorTests.test_doctor_names_the_removed_engine`: with
   `realtime_engine="openai"`, the output contains `removed in 2.0.0`.

### Moved, not deleted

`EchoRiskTests` (tests/test_realtime.py:818, 6 tests) moves to
`tests/test_listen_local.py`, patching `listen_local.default_sink`.
`test_the_text_is_shared_with_the_local_engine` (:746) moves to
`tests/test_local_engine.py` against `local_engine.watch_message` and
`watch_headline`. `tests/test_terminal.py:429,447` and
`tests/test_local_engine.py:670,1543-1547` change their import to
`local_engine` and drop the `RealtimeSession` assertion. The expected count is
`1119 - 98 + 7 (moved) + 5 (new) - 2 (replaced wiring and notice tests) = 1031`.
The plan pins the exact figure against the tree it lands on.

### Mutation checks

Each one is made by hand on the implementation branch and reverted:

- `cmd_run` falls through to `local_engine.run` for `"openai"`: test 1 fails.
- One key removed from `RETIRED_KEYS`: test 3 fails.
- The old `ears` lines put back in doctor: test 4 fails.
- `echo_risk` returns `""` unconditionally: the moved `EchoRiskTests` fail.
- `from .realtime import ...` put back in local_engine (with a stub module):
  test 2 fails.

### Runs

All runs export `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`:

- `nix develop -c pytest tests -q`: all pass, count as above.
- `nix develop -c python3 -m unittest discover -s tests`: the same count, OK.
- `nix flake check --no-write-lock-file`: passes, which proves the package
  builds without `websockets`.
- `grep -rn "realtime" src/`: only `realtime_engine` and the historical or
  removal comments. `grep -rn "import.*realtime\|from .realtime" src tests`:
  nothing.
- `omarchy-voice doctor` on p620 after deploy: no `OpenAI Realtime` line, and
  the engine section is local. The p620 config is unchanged.
