---
status: draft
issue: 136
intent: intent/2026-09-24-136-resident-piper.md
---

# Spec: a resident Piper worker, spoken to over its own pipes

Closes #136. Line numbers are from `perf/136-resident-piper` at `3656e82`
(`main` `be27af4`, v2.0.0, plus the approved intent). Gate B of #79 is still
overridden by the owner's choice, as the intent records. Nothing here claims
that the gate opened.

Baseline: 1084 tests collected (`nix develop -c python3 -m pytest tests -q
--collect-only`, with `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`).

## What was measured for this spec

I ran a scratch script (not committed, output to `/dev/null`, nothing played,
no process left running) on piper's own interpreter from piper-tts 1.8.0. It
used piper's site dirs and `PiperVoice.load` on
`en_GB-jenny_dioco-medium`, with the model in page cache:

| Step | Time |
| --- | --- |
| import piper + load the voice, once | 1.44 s |
| "Done." (0.35 s of audio) | 0.024 s, 0.027 s |
| a 2.3-2.5 s sentence | 0.098 s, 0.164 s |
| a 4.0-4.1 s sentence | 0.167 s, 0.171 s |
| peak RSS of the process after six sentences | 192 MB |

Each sentence came back as **one** `AudioChunk`. So the first sample waits
for the whole sentence's synthesis, both today and after this change. The
first sample is still about 1.4 s sooner, because the load has already been
paid.

The source of the installed package (`piper/__main__.py:184-193`) confirms
what the intent says. `--output-raw` writes each line's chunks and flushes,
but it writes no end marker. `piper/http_server.py` takes `--host` and
`--port` and calls `app.run(host=..., port=...)` (`:354`). Werkzeug 3.1.8 does
accept a `unix://` host (`werkzeug/serving.py:659`). The server still has
`POST /download`. This design does not use it.

**Target.** With the worker loaded, the time from handing a sentence to the
mouth to its first sample drops from about 1.6 s to that sentence's synthesis
time: 0.03-0.17 s on this machine for sentences up to 4 s long. SPEAK per
Piper sentence drops by about 1.4 s.

## Decisions on the intent's open questions

The intent was approved without answers to its questions, so each one is
decided here with the reasoning. Any of them can be rejected at this gate.

1. **Framing: (b), a small worker of ours on piper's interpreter, framed by a
   length prefix on its stdout.** It uses pipes only, so there is no socket,
   no port and no token. Nothing another local program or a web page can
   reach, which meets #72's reasoning with nothing left over (whisper-server's
   `/proc` caveat, `listen_local.py:175-177`, does not arise). It adds no new
   dependency: the worker imports only the standard library and `piper`, and
   runs on the interpreter that piper-tts already brings into the closure. It
   keeps streaming as it is today. Chunks are written the moment piper yields
   them, and the daemon hands them to `pw-cat` as they arrive.
   - (a), loading the voice in the daemon, is rejected. It needs piper and
     onnxruntime on our own interpreter. That is a new dependency, and it
     ties our Python version to piper's. It also puts 135-190 MB and a native
     ONNX runtime inside the daemon, so a segfault in onnxruntime takes the
     mic gate, the brain session and the log down with it. In a child
     process, the same crash costs one sentence, spoken the old way.
   - (c), `piper.http_server`, is rejected even when bound to 127.0.0.1 or a
     `unix://` socket (verified above). It returns a whole WAV per request,
     so there is no streaming. It adds Flask to the daemon's life. It still
     serves `/download` and `/voices` to anything that can reach the socket.
     And it would need #72's token prefix, which Flask's routes do not take.
     A Unix socket fixes the web-page threat but none of the rest, and it is
     more code than a pipe.
   - (d), inferring the end of a sentence from silence or a sentinel, is
     rejected. It is fragile, as the intent says. A sentinel sentence costs
     its own synthesis, and a sentence can contain silence of its own.
2. **Ownership: `Feedback` owns the worker, and it follows #72's `Server`
   (`listen_local.py:167-243`), with one difference.** There is `start()`
   (returns None on any failure), `alive`, `stop()` (terminate, wait 2 s,
   then kill) and a `failed` flag. **There is no automatic restart.** Once the
   worker dies, fails to start, or times out, `failed` is set and every later
   Piper sentence uses the per-sentence path for the rest of the daemon's
   life, with one `warn` line in the log. That is exactly what #72 does for
   whisper (`local_engine.py:532-541`). Reasons: a worker that died once on
   this model and this machine (OOM, a broken voice) will very likely die
   again, and a restart loop would pay 1.4 s and a failure on every sentence.
   Failing costs only the gain, never speech. And `systemctl --user restart`
   of the daemon resets it. The difference from #72 is **dying with the
   daemon, including a crash**: the worker exits when its stdin reaches EOF.
   The kernel closes that pipe when the daemon dies in any way, SIGKILL
   included. So no `prctl`, no pidfile and no reaper are needed. `stop()` in
   the engine's `finally` (`local_engine.py:977-990`, next to
   `server.stop`) covers a clean exit.
3. **Memory and when it starts: only where Piper speaks.**
   - **Piper is the first voice** (no `tts_command`, and
     `elevenlabs.ready()` is false, the same test as `voice_chain`,
     `local_engine.py:997-1013`): the worker starts at daemon start. It is a
     `to_thread` beside `listen_local.Server.start` (`:953-955`), hidden
     behind the 6.5 s brain warm-up, and logged
     `start   piper resident` or `start   piper per sentence`.
   - **Piper is the fallback** (this machine): the worker starts lazily,
     at the first sentence that reaches `_speak_piper`, and is kept from
     then on. That sentence waits for the load (about 1.4 s, less than
     today's 1.6 s per-sentence start), and it already has a failed cloud
     request in front of it. The next sentences do not wait.
   - So p620 and razer hold **0 MB** until ElevenLabs fails. An install
     with no key holds about 135-190 MB (the intent's 134-138 MB for the CLI
     at rest, and 192 MB peak measured above after a 4 s sentence) for the
     daemon's life. That is the price of the gain, and it is only paid where
     the gain is used.
   - With `OMARCHY_VOICE_PIPER_PYTHON` unset (the dev shell, a non-Nix
     install, the tests), no worker is ever started, and everything is as
     today.
4. **espeak-ng is out of scope and stays per sentence.** It is only reached
   when piper or its model is missing (`feedback.py:204-206`). The package
   always ships both (`nix/package.nix:80-81`, `:101-102`), so on a packaged
   install espeak-ng never speaks. It also has no model to load, so it has
   no 1.6 s start-up to remove.
5. **Piper gets no SYNTH span. #79's span definitions do not change.** A
   SYNTH span for Piper would change what `first_audio` means for Piper
   sentences (`trace.py:119-134`) in the very change being measured, so the
   before and after would not measure the same thing. The measurement is
   `speak=`, which already contains start-up + synthesis + playback. The
   saving is start-up, and it shows there. A SYNTH span for the resident
   worker can be a later issue, once this has landed and a new baseline
   exists.

## Design

### The worker: `src/omarchy_voice/piper_worker.py` (new, about 30 lines)

It runs on piper's interpreter, not ours. So it imports nothing from
`omarchy_voice`, only `sys`, `struct` and `piper`. `argv[1]` is the model
path.

- Load `PiperVoice.load(model)`, then write one end marker (see below) as
  "ready".
- Loop: `line = sys.stdin.buffer.readline()`. On `b""` (EOF), exit 0. This
  is how the worker dies with the daemon.
- For each `AudioChunk` of `voice.synthesize(line.decode().strip())`, write
  `struct.pack(">I", len(b)) + b` with `b = chunk.audio_int16_bytes`. Then
  write the end marker `struct.pack(">I", 0)`, and flush.
- An empty line gets only the end marker. There are no other writes to
  stdout (`stderr` goes to `DEVNULL` in the parent).

A zero-length chunk is never written as a chunk, so a length of 0 is always
the end marker.

### The owner: `feedback.PiperWorker` (in `feedback.py`)

Modelled on `listen_local.Server`:

- `start(model, timeout=10.0) -> PiperWorker | None`. Needs
  `OMARCHY_VOICE_PIPER_PYTHON` set, the model, and `pw-cat`. It runs `Popen(
  [python, <path of piper_worker.py>, model], stdin=PIPE, stdout=PIPE,
  stderr=DEVNULL)` and waits for the ready marker with a deadline. Any
  failure (OSError, EOF, timeout, a non-zero marker) calls `stop()` and
  returns None.
- `speak(text, rate)`. It sends `text` with every `\r` and `\n` replaced by a
  space, UTF-8, then `\n`. It opens the same `pw-cat --playback --raw ...`
  as today (`feedback.py:185-194`), writes each framed chunk into it as it
  arrives, closes its stdin at the end marker, and waits for `pw-cat`.
  Every read of the worker's stdout goes through `select` with a 10 s
  deadline. A worker that hangs cannot hold the mouth, and so the #114
  microphone gate, forever.
- `alive`, `failed` and `stop()` behave as in `Server`. `stop()` closes the
  worker's stdin first, so a healthy worker exits on EOF before any
  `terminate`.
- A `threading.Lock` around `speak`. `_speak_now` runs on the `_speech_loop`
  thread and on `Feedback.speak`'s threads, and the worker's pipe can only
  carry one sentence at a time. `ponytail:` one global lock. Two sentences
  never overlap on the speakers anyway.

### `Feedback._speak_piper` (`feedback.py:170-206`)

In order:

1. If `self.piper` is None and not `self._piper_failed`, try
   `PiperWorker.start` (this is the lazy start).
2. If there is a worker, call `speak(text, piper_rate(model))`. On any
   failure (EOF, timeout, a broken pipe, a bad frame), stop the worker, set
   `_piper_failed`, and log
   `warn    tts: piper resident failed (<reason>) — per sentence`. Then:
   - if **no bytes** reached `pw-cat` yet, speak the sentence on today's
     per-sentence path;
   - if some did, the sentence has been heard, because piper gives one chunk
     per sentence (measured above). It is not spoken twice. The warn line
     says why the rest is missing.
3. Otherwise, today's code, unchanged: per-sentence `piper | pw-cat`, then
   espeak-ng.

The text never reaches the log, and it never becomes a span name (#79). The
reason in the warn line is the exception's class or `timeout`, never the
input.

### Engine wiring (`local_engine.py:953-990`)

- If Piper is the first voice (decision 3), start the worker in a thread
  beside `listen_local.Server.start`, and await it with the whisper server.
- In the `finally`, call `feedback.stop_piper()` next to `server.stop`.
- `cli.py:206` and `local_engine.py:1044` build a `Feedback` only for state
  and notify. They never speak, so they never start a worker. Nothing else
  changes.

### Packaging (`nix/package.nix`)

Add one wrapper line: `--set-default OMARCHY_VOICE_PIPER_PYTHON <an
interpreter that can import piper>`. The first choice is
`${piper-tts.pythonModule.withPackages (_: [ piper-tts ])}/bin/python3`. If
nixpkgs' `withPackages` does not accept the application derivation, the
second choice is a `writeShellScript` that execs piper's own interpreter with
`PYTHONPATH=${python3Packages.makePythonPath [ piper-tts ]}`. Either way it
uses store paths already in the closure. There is no new package, and our own
interpreter's dependencies (`pyproject.toml`) do not change. The plan's first
step proves which one builds, with `$OMARCHY_VOICE_PIPER_PYTHON -c 'import
piper'`.

### What does not change

`_speak_now`'s order (tts_command, ElevenLabs, Piper, espeak-ng), the text
spoken, `pw-cat`'s arguments, the SPEAK span in `_speech_loop`
(`local_engine.py:299-324`), `_voice_until`, the #114 gate, barge-in, and
`trace.py`.

## Alternatives rejected

- **(a) in-process, (c) `http_server`, (d) silence or sentinel**: see
  decision 1.
- **A resident `pw-cat` as well.** The end of playback is how the mouth
  knows the sentence is over: the SPEAK span, `_voice_until` and #114's
  echo tail all depend on it. A long-lived `pw-cat` has no such end.
  Starting `pw-cat` is cheap next to 1.6 s.
- **Resident `piper --output-raw` with a per-sentence `pw-cat`, ended by a
  byte count estimated from the text.** That is a guess, and a wrong guess
  either cuts her off or hangs the mouth.
- **Finding piper's interpreter at runtime by reading its nixpkgs wrapper
  (`.piper-wrapped` line 3).** It works, and the measurement above used it.
  But it depends on nixpkgs' wrapper internals. The package knows the path at
  build time.
- **Restarting the worker every time it dies.** See decision 2.
- **Always starting the worker, as with whisper.** That costs 135-190 MB on
  every install, including this one, where Piper has not spoken in two weeks
  (decision 3).

## Risks

- **`withPackages` and a Python application.** Whether nixpkgs builds this
  cleanly for `piper-tts` is not verified here (see Packaging). Both options
  are named, and the plan checks them first. If neither builds, the variable
  stays unset and behaviour is exactly today's. So it fails safe, but with no
  gain.
- **A hung or slow worker holds the mouth for up to 10 s** before the
  fallback. That is the same bound as `Server.start` and the whisper request
  timeout. A healthy worker answers in under 0.2 s.
- **Memory on no-key installs:** 135-190 MB for the daemon's life (decision
  3). This is documented in the log line and in the README's voice section.
- **Piper's chunking.** If a later piper splits a sentence into several
  chunks, the protocol still works, because it frames every chunk. The
  "partly heard, not repeated" rule then drops the tail of the sentence, with
  a warn line. That only happens on a worker failure in mid-sentence.
- **Hosts.** On p620 and razer (ElevenLabs primary) this only acts after a
  cloud failure. On installs with no key it runs on every sentence.

## Verification

### Tests, with fakes only

No real piper and no audio. `subprocess.Popen` is faked for `pw-cat`, and the
worker is either a fake object or the real `piper_worker.py` run with
`sys.executable` and a **fake `piper` package** put first on `PYTHONPATH` in a
temp dir. The fake's `PiperVoice.load` returns an object whose `synthesize`
yields fixed bytes. All tests run with
`DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. Tests written first, and
each must fail on `main`:

1. `test_worker_frames_each_sentence` (real worker, fake piper): two lines in
   give ready, chunk(s), 0, chunk(s), 0, and the bytes match the fake's
   output.
2. `test_worker_exits_on_eof`: closing its stdin ends it with code 0 within
   2 s. This is the "dies with the daemon" guarantee.
3. `test_resident_piper_is_used_for_the_second_sentence`: a fake worker
   started once. Two `_speak_piper` calls give one `start` and two `speak`
   calls, and no per-sentence `piper` Popen.
4. `test_no_python_means_no_worker`: with `OMARCHY_VOICE_PIPER_PYTHON`
   unset, the per-sentence path runs, as on `main`.
5. `test_dead_worker_falls_back_before_any_audio`: the worker gives EOF
   before its first chunk. The sentence is spoken by the per-sentence piper,
   `failed` is set, one warn line is logged without the sentence text, and the
   next sentence does not try the worker again.
6. `test_partly_heard_sentence_is_not_repeated`: one chunk, then EOF. No
   per-sentence piper for that sentence, and a warn line.
7. `test_hung_worker_times_out`: no bytes, and the deadline is patched
   small. There is a fallback, and the worker is killed.
8. `test_worker_starts_at_daemon_start_only_when_piper_is_first`: with
   ElevenLabs ready, no start at daemon start. Without it, one start.
9. `test_engine_stops_the_worker`: the engine's `finally` calls
   `stop_piper` on both a clean exit and an error exit.
10. `test_sentence_never_logged`: across 5-7, the sentence text is in no log
    line and no span name.
11. The existing `SpeakingIntoAnOpenMicTests`, `VoiceSelectionTests`,
    `SampleRateTests` and every #114, #86 and #79 test pass unchanged.

### Mutation checks

Each must turn at least one test red, and is then reverted:

- the worker ignores EOF (`continue` on `b""`) → 2;
- `_speak_piper` always starts a new worker → 3;
- no fallback when the worker dies before any bytes → 5;
- fall back even after bytes were played → 6;
- drop the `select` deadline → 7;
- start the worker regardless of the voice chain → 8;
- remove `stop_piper` from the `finally` → 9;
- put `text` into the warn line → 10.

### Runners

- `nix develop -c pytest tests -q`: all pass, 1084 plus the new tests.
- `nix develop -c python3 -m unittest discover -s tests`: the same.
- `nix flake check --no-write-lock-file` and `nix build`. Then
  `result/bin/omarchy-voice`'s wrapper sets `OMARCHY_VOICE_PIPER_PYTHON`, and
  that interpreter imports `piper`.
- `pgrep -f piper_worker` is empty after the test run.

### Before and after, with Piper forced (run by the owner; this plays audio)

Both builds are v2.0.0 `main` (`be27af4`) and the branch, built with
`nix build`. The config is a temp `XDG_CONFIG_HOME` whose `config.toml` has
`[elevenlabs] enabled = false`, no `tts_command` and `trace_timings = true`.
So the `start` line reads `voice=piper`.

1. **Fixed sentences, no brain.** A scratch driver calls
   `Feedback(config)._speak_now(s)` for the same 10 fixed sentences (from 1
   word to about 25 words), 3 rounds on each build, and records the wall time
   per sentence. Expected: the branch's median is about 1.4 s lower
   (at least 1.2 s), and the gap does not grow with sentence length.
2. **The daemon, traced.** On each build: start the daemon, wait for
   `start   brain ready` (and, on the branch, `start   piper resident`), run
   the same 5 typed commands with `omarchy-voice listen say ...`, then run
   `tools/timing_report.py`. Expected: the median `speak=` per sentence is
   about 1.4 s lower on the branch. `first-audio=` is **not** compared: for
   Piper it leaves out start-up on both builds (decision 5).
3. After each run, no `piper_worker` process is left once the daemon has
   stopped, including after `kill -9` of the daemon (the EOF path).

If step 1 does not show at least 1.2 s, the change is not merged on faith.
The result is recorded on #136.
