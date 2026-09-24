---
status: approved
issue: 72
author: olafkfreund
---

# Intent: stop spending seconds between the user finishing and the model starting

Closes #72.

## Problem

After the user stops talking, the local engine waits, transcribes, and only
then hands the words to the model. Measured on this machine today, that gap is
roughly **1.8 s when whisper's model is in the page cache and 2.9 s when it
is not**. The first number assumes a warm cache, the second a cold one; both
are measured, not estimated. On top of that, the transcript is wrong in the one
place it most needs to be right, and the wake word throws away half of what was
said.

### 1. A fixed 1.5 s of silence before anything happens

`record_utterance` (`src/omarchy_voice/listen_local.py:108-161`) ends a
sentence when the RMS level has stayed under `silence_level` for `hang`
seconds. The instruction path uses `silence_hold_seconds = 1.5`
(`config.py:305`), and the wake path uses `DEFAULT_HANG_SECONDS = 1.2`
(`listen_local.py:38`). Every turn begins with that much dead air, by
construction. It is now the largest fixed cost in this gap.

### 2. whisper is started from nothing for every utterance

`transcribe` (`listen_local.py:164-194`) writes a WAV to a temp directory and
runs a fresh `whisper-cli`, which loads the 148 MB `ggml-base.en.bin` each
time. These are 3.3 s utterances on this machine (whisper-cpp 1.9.2, 128
cores):

| How | Time |
|---|---|
| `whisper-cli`, model not in page cache (first run) | **1.37 s** |
| `whisper-cli`, model in page cache | 0.32 s |
| `whisper-cli -t 16` instead of `-t 4` | 0.31 s (threads are not the constraint) |
| `whisper-server` (same package), resident, per request | **0.05 s** |

The review that raised #72 said "reloads the model, ~1.5 s every turn". That
is only true when the cache is cold. The daemon sleeps for long stretches, so
the cold case is the first turn after a pause, which is exactly when someone
is waiting. `whisper-server` ships in the same `whisper-cpp` package as
`whisper-cli`.

### 3. "Claude" is heard as "clone" or "cloud"

No `--prompt` is passed, so whisper has no idea which words this desktop uses.
From `session.log`: `'Perfect. Start a new cloud session.'`. Reproduced with a
synthetic clip of "Start a new Claude session in the terminal on workspace
three":

```
-t 4                         Start a new clone session in the terminal on workspace 3.
-t 4 --prompt "Claude, …"    start a new Claude session in the terminal on workspace three
```

The prompt costs 0.13 s and fixes it. Every misheard name costs a model turn
spent guessing, or a repair turn from the user, and those cost 1.5-7.6 s
each (intent #23).

### 4. "Oma, turn it down" loses "turn it down"

`_wake_turn` (`local_engine.py:305-331`) records until silence, transcribes,
and if the wake word is in the transcript it only switches listening on. The
rest of the transcript is dropped. The user then has to wait for the listening
state and say the instruction again, which costs a second hang and a second
transcription. Asking in one breath, the way people talk to assistants, does
not work. The log does not show this being hit: it has one wake activation,
a bare "Oma?", and the toggle key is how this machine is mostly used. It is a
code fact, not an observed complaint, and it matters to anyone who uses the
wake word as designed.

### 5. The trace cannot see any of this

`trace_timings` starts its clock after transcription
(`local_engine.py:355`). The silence hold and whisper, the whole subject of
this issue, are outside every trace. Nothing that changes them can be judged
against a real task.

## Proposed outcome

- The gap between the user finishing and the model starting is measured
  inside the trace, per turn, and is clearly smaller than today's
  1.8 s warm / 2.9 s cold.
- Transcription does not reload the model per utterance, and the first turn
  after a long pause is not slower than the rest.
- Names this desktop actually uses (Claude, Hyprland, Omarchy, the
  configured wake word, installed app names) come out spelled right.
- "Oma, <instruction>" in one breath runs the instruction.
- Ending a sentence is not a fixed 1.5 s wait, and a user who pauses
  mid-sentence is not cut off any more often than now.

## Affected users and systems

- Everyone on `[realtime] engine = "local"` (the default), and
  `omarchy-voice ask` (`cli.py:51-61`), which uses the same recorder and
  transcriber.
- `src/omarchy_voice/listen_local.py`, `local_engine.py` (`_turn`,
  `_wake_turn`, the trace), `config.py` (the hold settings), and
  `nix/package.nix` / `nix/hm-module.nix` if a resident transcriber becomes
  something the module starts.
- Not the OpenAI realtime engine, which has its own server-side turn
  detection.

## Constraints

- **Nothing leaves the machine.** Local listening exists so that audio is
  never uploaded (`listen_local.py:1-13`). A resident transcriber listens
  on loopback or a Unix socket, never on a routable address.
- **The wake word must not get more eager.** A transcript without the wake
  word must still never reach the brain. The "Thank you." / `[INAUDIBLE]`
  rejection in `clean()` (`listen_local.py:207`) and `MIN_SPEECH_SECONDS` stays.
- **No new model download at build time** unless the approver agrees. The
  current model is packaged by `nix/whisper-model.nix`.
- If a resident transcriber is unavailable, local listening must still work,
  even if more slowly. It must never go deaf because a helper process died.
- The vocabulary is read from what the machine has (the manifest, the config),
  not a hard-coded list that drifts.
- Memory: a resident `base.en` is about 150-200 MB for as long as the
  daemon runs.

## Open questions

1. **How is the end of a sentence detected?** Either (a) lower the fixed hold,
   for example 1.5 → 0.8 s, which is a one-line default change with the
   risk of cutting slow speakers off; or (b) use a real voice activity
   detector. whisper-cpp 1.9.2 ships Silero VAD support
   (`whisper-vad-speech-segments`), and research found learned end-of-turn
   models such as smart-turn getting dead air down to about 0.45 s. (a) is
   cheap and measurable today; (b) is the bigger win and a bigger change.
   Which, or (a) now and (b) as its own issue?
2. **What keeps whisper resident?** `whisper-server` as a second process the
   daemon starts and owns, or a systemd user unit from the Home Manager module?
   The first keeps packaging untouched. The second makes it visible and
   restartable on its own.
3. **Speaking is out of scope here. Should it be?** #72 also named Piper
   being spawned per sentence, and the 0.35 s echo tail. On this machine the
   voice is ElevenLabs, with Piper only as the fallback, and ElevenLabs
   fetching the whole clip before playing is already a documented ceiling
   (`elevenlabs.py:120-122`). My proposal: leave the output side to a separate
   issue, measured once this one's trace can see a whole turn.
