---
status: approved
issue: 136
author: olafkfreund
---

# Intent: keep Piper resident, so a Piper sentence does not pay 1.6 s of start-up

Closes #136. Follow-up from #79 (PR #133), decision 2 of
`plan/2026-09-24-79-speaking-side-unmeasured.md`.

## Gate B is overridden, on purpose

#79's approved plan opened this work only behind **gate B**: Piper becomes the
configured primary voice, or `tts     elevenlabs failed` appears in more than
5 % of spoken sentences over a week. **Neither is true here.** On this machine
the voice is ElevenLabs and Piper is the fallback. Every `start` line in
`~/.local/state/omarchy-voice/session.log` says `voice=ElevenLabs
eleven_turbo_v2_5, piper if it fails` (58 of them, 2026-09-11 to 2026-09-22),
and the log has **0** `elevenlabs failed` lines in 3046.

**The owner chose on 2026-09-24 to build this now anyway, overriding gate B.**
It is recorded here so nobody later reads this as the gate having opened. What
that means:

- On this machine, the change speeds up a path that has not run in two weeks.
  The win is for installs with no ElevenLabs key, where Piper *is* the voice
  (`voice_chain`, `local_engine.py:997-1013`, puts Piper first when
  `elevenlabs.ready()` is false), and for the afternoon the cloud does fail.
- It cannot be measured from normal use here. It has to be measured by
  forcing Piper (see the outcome).

## Problem

`Feedback._speak_piper` (`feedback.py:170-203`) starts a new
`piper --model <onnx> --output-raw` for every sentence, pipes it into a new
`pw-cat`, writes the sentence, closes stdin and waits for both to exit. So
every sentence pays for a Python start, the piper imports and loading the
63 MB ONNX voice, before a sample is made.

Measured on p620 (piper-tts 1.8.0, `en_GB-jenny_dioco-medium`, model in page
cache, output to a pipe, nothing played, scratch script not committed):

| Input on stdin | Wall time | Audio out | Peak RSS |
| --- | --- | --- | --- |
| empty (start, load, exit) | 1.68 s | 0 | 134 MB |
| one sentence | 1.62 s, 1.66 s | 1.37 s, 1.29 s | 138 MB |
| two lines | 1.66 s | 2.66 s | 138 MB |

Synthesis itself is lost in the noise; **the 1.6 s is start-up, paid again on
every sentence.** A three-sentence Piper reply waits about 5 s longer than it
has to, 1.6 s of it before the first word.

Why it was not done in #79: `--output-raw` (`piper/__main__.py` in the
package) reads stdin line by line and writes each line's samples as they are
made, but writes nothing that says where a sentence ends. A process that stays
up needs **framing** (to know when sentence N is done, to return from the
mouth and start the next wait), **restart** when it dies, and an **owner** that
starts and stops it.

What the installed piper offers, checked from the package and its docs
(`docs/API_HTTP.md` in OHF-Voice/piper1-gpl):

- `python3 -m piper.http_server -m MODEL` ships in the same package, with
  Flask. `POST /synthesize` returns a whole WAV per request: framed, but not
  streamed, so the first sample waits for the whole sentence. It binds
  `0.0.0.0:5000` by default, has no authentication, and has `POST /download`,
  which fetches voices from the network. That is the #72 problem again, worse.
- The `piper` Python package (`PiperVoice.load`, `voice.synthesize()` yielding
  one `AudioChunk` per sentence) is importable from piper's own environment.
  Our package's interpreter does not currently have it.
- No stdin/stdout mode with framing.

## Proposed outcome

- A Piper sentence no longer pays the start-up. With the model loaded, the
  gap from handing a sentence to the mouth to its first sample drops from
  about 1.6 s to the synthesis time of that sentence (a small fraction of a
  second on this machine; the spec states the target from a measurement).
- Replies, barge-in, the #114 microphone gate and the log sound and behave as
  before. Only the wait before each Piper sentence changes.
- If the resident Piper dies or will not start, speech still happens: the
  per-sentence path, and then espeak-ng, as today. Never silence.
- **How it is measured:** #79's SPEAK spans. Piper sentences are one SPEAK
  span each (no SYNTH spans inside, unlike ElevenLabs), from handing the
  sentence to the mouth to the mouth returning, so SPEAK = start-up +
  synthesis + playback. The TIMING line in `session.log` reports `speak=` per
  turn. Because ElevenLabs never fails here, the before/after comparison forces
  Piper (no ElevenLabs key for the run) and speaks the same fixed replies on
  the build before and after. The saving should show as about 1.6 s per
  Piper sentence in `speak=`. It will **not** show in `first-audio=`: for
  Piper that is a lower bound that leaves out its start-up
  (`trace.py:120-127`). Note: the daemon running today is 0.3.0 and predates
  #79, so the log holds **0** TIMING lines. The baseline needs the v2.0.0
  build.

## Affected users and systems

- `src/omarchy_voice/feedback.py` (`_speak_piper`, `_speak_now`), and whatever
  owns the process: `local_engine.py`'s start-up and shutdown
  (`:953-988`, where `listen_local.Server` is started and stopped), plus the
  realtime engine and the CLI, which also build a `Feedback`.
- `nix/package.nix` if the Python piper API or `http_server` becomes a
  dependency of our interpreter.
- Users with no ElevenLabs key (Piper is their voice): the main gain.
- This machine (p620) and razer: only when ElevenLabs fails, or when Piper is
  forced for the measurement.
- Memory: about 135 MB held for the daemon's lifetime, on every install that
  has Piper, used or not.

## Constraints

- Never silence: every failure of the resident process falls back to what
  happens today.
- Loopback or no network at all. Nothing another local page can reach and
  make speak or download (#72's reasoning for whisper-server).
- No leftover processes: the resident Piper dies with the daemon, including
  on a crash.
- No change to what is spoken, the #114 microphone gate, barge-in, or the
  SPEAK/SYNTH span definitions from #79, so before and after stay comparable.
- The sentence never reaches the log or a span name (#79).
- Tests must not start a real piper or play audio (#99).

## Open questions

1. **Framing.** Which way does the spec take?
   (a) Load the voice in our own process with the `piper` Python API, which
   yields one chunk per sentence: framing for free, no second process, but a
   new dependency on our interpreter, and synthesis runs in the daemon.
   (b) A child that runs a tiny script of ours on piper's interpreter and
   writes a length prefix per sentence on stdout.
   (c) `piper.http_server` on 127.0.0.1: framed per request, but a whole WAV
   (no streaming), no token, and a `/download` route.
   (d) Keep `--output-raw` and infer the end from silence or a sentinel
   sentence: fragile.
2. **Restart and ownership.** Mirror #72's `listen_local.Server`: started at
   daemon start, stopped at shutdown, `alive()` checked before use, fall back
   when it is dead? Or start it lazily at the first Piper sentence, which on
   this machine may be never? Restart once, or every time it dies?
3. **Memory.** About 135 MB held for the daemon's life. Always, only when Piper
   is the primary voice, or loaded on the first ElevenLabs failure and kept
   from then on? The last costs 1.6 s once, on the sentence that already had
   a failed cloud request in front of it.
4. **espeak-ng.** It is only reached when Piper or its model is missing
   (`feedback.py:204-206`). The package puts it on PATH beside piper
   (`nix/package.nix:80`), so on a packaged install it never speaks. Leave it
   per-sentence and out of scope? It is not installed in this dev shell.
5. **Piper's own span.** Should Piper get SYNTH spans like ElevenLabs, so
   `first-audio=` counts its wait? That makes the gain visible per sentence,
   but it changes what #79's numbers mean, so a before/after comparison would
   have to use `speak=` only.
