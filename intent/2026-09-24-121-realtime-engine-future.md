---
status: approved
issue: 121
author: olafkfreund
---

# Intent: decide whether the OpenAI realtime engine stays supported

Closes #121. Related: #77 (its scope depends on this decision), #113.

## Problem

`omarchy-voice run` has two engines. The local engine is the default and is
the one in use. The OpenAI realtime engine is a second, full implementation
that must be kept up to date alongside it. Nobody has decided whether it
still earns that cost. Until someone does, every safety fix is designed,
reviewed and tested twice.

Line numbers are from `origin/main` at 6a9a3f5.

### What it costs

- `src/omarchy_voice/realtime.py` is 1,708 lines, the second-largest module
  after `tools.py`. Its tests are `tests/test_realtime.py` (1,197 lines,
  96 tests) and `tests/test_realtime_wire.py` (171 lines, 2 tests). That is
  98 of the suite's 950 tests.
- It has its own copy of the persona (`REALTIME_PERSONA`, realtime.py:177-254),
  its own tool converter (`to_realtime_tools`, realtime.py:301), its own gate
  tools (`GATE_TOOLS`, realtime.py:265) and its own hold wording
  (`HOLD_INSTRUCTION`, realtime.py:258). #77 lists these.
- Every recent consent fix needed separate work for this engine. #86 fixed
  spoken confirmation on the local engine only and split the realtime half
  into #113. #113 (15a2985, merged today) now makes `confirm_last` refuse
  every time on realtime (realtime.py:265-282, `_confirm` at :1276). Only
  `_local_confirm` (realtime.py:954) can release a held action: the bar
  widget, the keybind or `omarchy-voice listen confirm`. So on this engine,
  a spoken "confirm" no longer confirms anything.
- The engine gets little new work. It has 16 commits in its history. The
  two most recent (#74 and #113, both 2026-09-24) are fixes that came from
  the local engine. Before those, the last change was 2026-09-20 (#28, #30).

### How it is used on this machine

- The config chooses the engine with `[realtime] engine`
  (`realtime_engine`, config.py:407, default `"local"`). `cmd_run`
  (cli.py:188-201) starts realtime only on the exact string `"openai"`.
  Anything else, including a typo, starts the local engine.
- p620's config (`~/.config/nixos/hosts/p620/nixos/nixarchy.nix`, read-only)
  never sets `engine`, so it runs the local engine. The comments still
  describe it as "speech to speech against the OpenAI Realtime API"
  (nixarchy.nix:183) and "[realtime] voice, which arrives from OpenAI"
  (:205). It still passes `apiKeyFile` (:197), and `say` uses that key
  for its planner fallback.
- `~/.local/state/omarchy-voice/session.log` records 21 realtime starts, the
  last at 2026-09-12 16:26. It also shows 22 `session_expired` errors from
  OpenAI's 60-minute session cap. Every start since 2026-09-12 15:50 has been
  `engine=local`, 37 in all.

### What only realtime offers

config.py:397-406 states the trade-off:

- It hears audio, not a transcript, so it picks up tone, hesitation and
  accent.
- It has native barge-in, because the microphone never closes.
- It replies in roughly 1-2 s (README "Speech without OpenAI").

It also has costs:

- Audio is metered, including room tone.
- Room audio streams to OpenAI the whole time the microphone is open.
- It has no wake word (README:79-81).
- Sessions are capped at 60 minutes.
- Since #113, spoken confirmation is not possible.

### Where it reaches outside its own file

- `local_engine.py:43` imports `ECHO_TAIL_SECONDS`, `WATCH_POLL_SECONDS`,
  `_run_until_done`, `watch_headline` and `watch_message` from `realtime.py`.
  The local engine therefore depends on this module even when realtime is
  never run.
- `doctor` prints `realtime model ..., voice ...` under an "openai" heading
  (cli.py:367). It also branches on `realtime_engine != "openai"`
  (cli.py:430).
- README mentions `realtime` or `openai` 67 times. That includes the
  `engine = "openai"` diagram (README:67-84) and caveats in the
  planner/Claude sections (README:199-205, 246-252, 373-376).
- The Home Manager module (`nix/hm-module.nix`) has no engine option.
  `settings` is freeform TOML, and its example sets `realtime.voice = "marin"`
  (hm-module.nix:40). `apiKeyFile` and `apiKeyEnv` (:73, :90) describe the
  OpenAI key, which `say` needs whatever this decision is.

## Proposed outcome

The owner makes a recorded decision on the realtime engine, and the repo
matches it:

- The README, `doctor`, the config comments and the HM module example all say
  the same thing about whether `engine = "openai"` is supported, and how far.
- A future safety fix states whether it needs a realtime half. Nobody has to
  rediscover that question each time.
- #77 knows whether it is merging two engines or three.

## Affected users and systems

- Anyone with `[realtime] engine = "openai"` set. No host in this fleet sets
  it. razer's config was not checked.
- `realtime.py`, `local_engine.py` (the imports above), `cli.py` (`run` and
  `doctor`), `config.py`, README, `nix/hm-module.nix` and the realtime tests.
- The `say` planner fallback (`planner.py`, `planner_model = "gpt-4.1"`,
  config.py:264) uses the OpenAI key but not this engine. It is out of scope
  unless the owner brings it in.
- Open work that touches consent or turn-taking: #77, #113, #114.

## Constraints

- p620's config is read-only for this task. It already uses the local engine
  and must work unchanged whatever is decided.
- A user who has `engine = "openai"` set must not end up with a daemon that
  silently does something different. `cmd_run` today falls back to the local
  engine without saying so. Whether that is acceptable is decided in the
  spec, not assumed here.
- The OpenAI API key must keep working for `say`'s planner fallback.
- The local engine's use of `realtime.py`'s helpers must survive any outcome.
- Safety behaviour must not get weaker on either engine. In particular,
  #113's rule that only the key releases a hold stays.

## Open questions

1. Keep, freeze (safety fixes only, no new features) or remove? This is the
   owner's product decision.
2. If frozen, what counts as a "safety fix" that must still be designed for
   realtime? For example, does a new confirm rule for the local engine need a
   realtime half?
3. If removed, what happens to `engine = "openai"` in an existing config: a
   hard error, a warning and then local, or silently local as a typo is
   today?
4. Does the answer depend on razer? Its config has not been checked for
   `engine = "openai"`.
5. Is audio-native hearing or native barge-in something the owner still
   wants on the roadmap? If so, removing the engine closes that off.
