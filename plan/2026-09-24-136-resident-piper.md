---
status: approved
issue: 136
spec: spec/2026-09-24-136-resident-piper.md
---

# Plan: a resident Piper worker, spoken to over its own pipes

Closes #136. Base: `main` `be27af4` (v2.0.0). The branch
`perf/136-resident-piper` is at `2e40444`, which is `be27af4` plus the
intent and the spec. Neither of those touches `src/`, `nix/` or `tests/`.
Every `file:line` below was checked against `be27af4`. Step 2 rebases onto
#138 and #135, so re-check them after that rebase.

Files that change: `src/omarchy_voice/piper_worker.py` (new),
`src/omarchy_voice/feedback.py`, `src/omarchy_voice/local_engine.py`,
`nix/package.nix`, `tests/test_feedback.py`, `tests/test_local_engine.py`,
`README.md` (one paragraph). `trace.py`, `listen_local.py`, `cli.py`,
`elevenlabs.py`, `pyproject.toml` and `flake.nix` do not change.

## Approved decisions, carried over from the spec

1. **The framing is a worker of ours on piper's interpreter, framed by a
   length prefix on its stdout.** It is `src/omarchy_voice/piper_worker.py`,
   about 30 lines. It imports only `sys`, `struct` and `piper`, and nothing
   from `omarchy_voice`. `argv[1]` is the model path. It calls
   `PiperVoice.load(model)` and then writes the end marker
   `struct.pack(">I", 0)` once, to mean "ready". Then it loops on
   `sys.stdin.buffer.readline()`. For each `AudioChunk` of
   `voice.synthesize(line.decode().strip())` it writes
   `struct.pack(">I", len(b)) + b`, where `b = chunk.audio_int16_bytes`.
   After the last chunk it writes the end marker and flushes. An empty line
   gets only the end marker. A zero-length chunk is never written as a chunk,
   so a length of 0 always means the end marker. Nothing else is written to
   stdout. Its stderr goes to `DEVNULL`. Pipes only: no socket, no port and
   no token (#72's reasoning), and no new dependency.
   Rejected, and not to be reintroduced: (a) loading the voice inside the
   daemon (a new dependency, it ties our Python to piper's, and an
   onnxruntime segfault would kill the daemon); (c) `piper.http_server`, even
   on 127.0.0.1 or `unix://` (it returns a whole WAV per request, adds Flask,
   still serves `/download` and `/voices`, and cannot take #72's token);
   (d) inferring the end of a sentence from silence or a sentinel sentence.
2. **`Feedback` owns the worker through `feedback.PiperWorker`, which is
   modelled on `listen_local.Server` (`listen_local.py:167-243`).** It has
   `start(model, timeout=10.0) -> PiperWorker | None`, `alive`, `failed`,
   `speak(text, rate)` and `stop()`. `start` needs
   `OMARCHY_VOICE_PIPER_PYTHON` to be set and non-empty, the model, and
   `pw-cat`. It runs
   `Popen([python, <path of piper_worker.py>, model], stdin=PIPE,
   stdout=PIPE, stderr=DEVNULL)` and waits for the ready marker, with a
   deadline. Any failure (OSError, EOF, timeout, a non-zero marker) calls
   `stop()` and returns None.
3. **There is no automatic restart.** Once the worker dies, fails to start,
   or times out, `Feedback._piper_failed` is set. Every later Piper sentence
   then uses the per-sentence path for the rest of the daemon's life, with
   one `warn` line in the log. #72 does exactly this for whisper
   (`local_engine.py:540-541`). `systemctl --user restart` of the daemon
   resets it.
4. **The worker dies with the daemon.** It exits 0 when its stdin reaches
   EOF. The kernel closes that pipe when the daemon dies in any way, SIGKILL
   included. So there is no `prctl`, no pidfile and no reaper. `stop()`
   closes the worker's stdin first, so a healthy worker exits on EOF. Then
   it does what `Server.stop` does (`listen_local.py:238-243`): terminate,
   wait 2 s, kill. The engine's `finally` (`local_engine.py:978-990`) calls
   `feedback.stop_piper()` next to `server.stop` (`:986-988`).
5. **The worker starts only where Piper speaks.**
   - Piper is the first voice (no `tts_command`, and `elevenlabs.ready()` is
     false, the same test as `voice_chain`, `local_engine.py:997-1013`): the
     worker starts at daemon start. That start is a `to_thread` beside
     `listen_local.Server.start` (`:953-955`) and is awaited with it
     (`:965`). It is logged `start   piper resident` or
     `start   piper per sentence`.
   - Piper is the fallback (p620, razer): the worker starts lazily at the
     first sentence that reaches `_speak_piper`, and it is kept from then on.
   - With `OMARCHY_VOICE_PIPER_PYTHON` unset (the dev shell, a non-Nix
     install, the tests), no worker is ever started, and everything is as
     it is today.
   - `cli.py:206` and `local_engine.py:1044` build a `Feedback` only for
     state and notify. Building a `Feedback` never starts a worker.
6. **espeak-ng is out of scope and stays per sentence** (`feedback.py:205-206`).
   It is reached only when piper or its model is missing. The package always
   ships both (`nix/package.nix:80-81`, `:101-102`).
7. **Piper gets no SYNTH span. #79's span definitions do not change.**
   `trace.py:119-134` (`first_audio`) is untouched. The measure is
   `speak=`.
8. **`PiperWorker.speak(text, rate)`**: it sends `text` with every `\r` and
   `\n` replaced by a space, UTF-8, then one `\n`. It opens the same
   `pw-cat --playback --raw --format s16 --rate <rate> --channels 1 -` as
   today (`feedback.py:181-193`, arguments `:188-189`), writes each framed
   chunk into it as the chunk arrives, closes pw-cat's stdin at the end
   marker, and waits for `pw-cat`.
9. **Every read of the worker's stdout goes through `select` with a 10 s
   deadline.** A hung worker cannot hold the mouth, and so the #114
   microphone gate, for longer than that.
10. **What a failure mid-speak does.** On any failure (EOF, timeout, a
    broken pipe, a bad frame) `_speak_piper` stops the worker, sets
    `_piper_failed`, and logs
    `warn    tts: piper resident failed (<reason>) — per sentence`. Then:
    - if **no bytes** reached `pw-cat` yet, the sentence is spoken on
      today's per-sentence path;
    - if some did, the sentence has been heard (piper gives one chunk per
      sentence, as measured), so it is **not** spoken twice.
11. **The sentence never reaches the log or a span name** (#79). The reason in
    the warn line is the exception's class name or `timeout`, never the
    input.
12. **One `threading.Lock` around `PiperWorker.speak`.** `_speak_now` runs on
    the `_speech_loop` thread and on `Feedback.speak`'s threads, and the pipe
    can carry only one sentence at a time. `ponytail:` one global lock; two
    sentences never overlap on the speakers anyway.
13. **`_speak_piper`'s order** (`feedback.py:170-206`): (1) if there is no
    worker and `_piper_failed` is not set, try `PiperWorker.start` (the lazy
    start); (2) if there is a worker, `speak(text, piper_rate(model))`, with
    decision 10 on failure; (3) otherwise today's code, unchanged:
    per-sentence `piper | pw-cat`, then espeak-ng.
14. **Packaging: one wrapper line, `--set-default OMARCHY_VOICE_PIPER_PYTHON
    <an interpreter that can import piper>`**, built only from store paths
    that are already in the closure. There is no new package, and
    `pyproject.toml` does not change. Which expression is used is settled
    by step 1 (see the flagged note there).
15. **Nothing else changes:** `_speak_now`'s order (tts_command, ElevenLabs,
    Piper, espeak-ng), the text spoken, pw-cat's arguments, the SPEAK span
    in `_speech_loop` (`local_engine.py:299-324`), `_voice_until`, the #114
    gate, barge-in, and `trace.py`.
16. **Rejected, and not to be reintroduced:** a resident `pw-cat` (its end
    is how the mouth knows a sentence is over); resident
    `piper --output-raw` ended by a byte count guessed from the text;
    finding piper's interpreter at runtime from `.piper-wrapped`; restarting
    the worker every time it dies; always starting it, as with whisper.
17. **The merge is gated on the owner's before/after** with Piper forced:
    at least 1.2 s saved per sentence (median), or it does not merge.

### Decisions this plan adds (implementation detail, within the spec)

18. **The worker is run with `-P`**:
    `[python, "-P", <piper_worker.py>, model]`. Without it, Python puts the
    script's directory, `src/omarchy_voice/`, first on `sys.path`, and our
    `trace.py`, `config.py`, `keys.py`, `tools.py` and `session.py` would
    shadow any module of those names that piper, onnxruntime or their
    dependencies import. `-P` exists since Python 3.11. piper's interpreter
    is 3.14.7.
19. **Reads are unbuffered.** The worker is opened with `bufsize=0`, and the
    owner reads with `os.read(fd, n)` after each `select`. `select` cannot
    see bytes already held in a `BufferedReader`, so a buffered read would
    make the deadline wrong. The deadline is **per read**
    (`PIPER_READ_TIMEOUT = 10.0`, a module constant so tests can patch it).
    A healthy worker answers in under 0.2 s.
20. **A failed eager start counts as a failure** (decision 3). `Feedback`
    gets `start_piper() -> bool`, which starts the worker, or sets
    `_piper_failed` and returns False. The engine calls it and logs the
    `start` line. There is no lazy retry after that.
21. **New state on `Feedback.__init__`** (`feedback.py:74-88`):
    `self.piper = None`, `self._piper_failed = False`. `stop_piper()` stops
    and clears `self.piper`. It is safe to call when there is no worker.

## Landing order

**Third: #138, then #135, then this, then #137.** Step 2 is a hard
precondition.

**Overlap with #135, precisely.** Both change `src/omarchy_voice/feedback.py`
and nothing else in common.
- #135 rewrites the ElevenLabs branch of `_speak_now` (`feedback.py:146-158`)
  and **deletes `Feedback._play`** (`:160-168`). The deletion ends one blank
  line above `def _speak_piper` (`:170`), which this plan rewrites. So git
  will report a conflict on the lines between `:158` and `:171`, although
  the two changes do not overlap in meaning. Resolve it by keeping #135's
  deletion and this plan's `_speak_piper`.
- Both may add imports to the block at `:13-20` (`select`, `struct` here).
  Keep the union, sorted.
- This plan must **not** call `_play`, which #135 deletes. `PiperWorker.speak`
  opens its own `pw-cat` (decision 8). Do not factor a shared pw-cat helper
  with #135's code in this change. That is a later cleanup, if ever.
- #135 does not touch `_speak_piper`, `__init__`, `piper_model` or
  `piper_rate`. This plan does not touch `_speak_now`.

#137 (prefetch) lands after this and rebases on it. This plan does not
depend on #137.

## Steps

0. **Baseline.** `git fetch origin`, then `gh issue view 136` → confirm #136
   is OPEN and the branch is `perf/136-resident-piper`. Then run
   `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`,
   `nix develop -c python3 -m pytest tests -q` and
   `nix develop -c python3 -m unittest discover -s tests`
   → verify by both passing, with **1084** tests at `be27af4`. Record any
   failure that already exists before continuing. `pgrep -f piper_worker`
   → empty.

1. **Resolve the packaging risk before any code (decision 14).** Put this in
   a scratch file, not the repo (`probe.nix`):
   ```nix
   let pkgs = (builtins.getFlake "<repo>").inputs.nixpkgs.legacyPackages.x86_64-linux;
       inherit (pkgs) lib piper-tts python3Packages;
   in {
     first  = piper-tts.pythonModule.withPackages (_: [ piper-tts ]);
     second = pkgs.writeShellScript "piper-python" ''
       export PYTHONPATH=${python3Packages.makePythonPath [ piper-tts ]}
       exec ${python3Packages.python.interpreter} "$@"'';
     named  = pkgs.writeShellScript "piper-python" ''
       export PYTHONPATH=${lib.makeSearchPath python3Packages.python.sitePackages
         ([ piper-tts ] ++ python3Packages.requiredPythonModules piper-tts.propagatedBuildInputs)}
       exec ${python3Packages.python.interpreter} "$@"'';
   }
   ```
   Run `nix-build --no-out-link probe.nix -A first` (and `-A second`,
   `-A named`). For each that builds, run
   `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent <out> -c 'import piper;
   from piper import PiperVoice; print(piper.__file__)'` (for `first`, the
   path is `<out>/bin/python3`).
   → verify by one expression that builds, imports `piper`, and prints a
   `piper/__init__.py` under the **same** `piper-tts-1.8.0` store path that
   `nix path-info -r` shows in `result`'s closure today. Use the first one in
   the order `first`, `second`, `named` that passes. **If none passes, stop
   and report. Do not implement.** The variable then stays unset, which is
   today's behaviour.

   > **Flagged: already run while writing this plan (nixpkgs from
   > `flake.lock` at `be27af4`, piper-tts 1.8.0, python 3.14.7).**
   > - `first` (the spec's first choice) **does not evaluate**:
   >   `attribute 'pythonModule' missing`. piper-tts is a Python
   >   application, it is not in `python3Packages`, and it has no
   >   `pythonModule`.
   > - `second` (the spec's fallback, as literally written) **builds, but
   >   does not import piper**: `makePythonPath` drops a derivation with no
   >   `pythonModule`, so `PYTHONPATH` held only python's own
   >   `site-packages`. The result was `ModuleNotFoundError: No module named
   >   'piper'`.
   > - `named` is the spec's fallback in the shape the spec gives
   >   (`writeShellScript`, piper's interpreter, `PYTHONPATH`, store paths
   >   already in the closure), with the path computed by hand. It **builds
   >   and imports piper** from
   >   `/nix/store/58ji2wj2…-piper-tts-1.8.0`, the same path the package
   >   already ships.
   > - `python3Packages.toPythonModule piper-tts` makes both of the spec's
   >   expressions work, but it produces a **different** piper-tts
   >   derivation (`khqcv2kh…`), which is a second copy in the closure. That
   >   goes against decision 14, so it is rejected.
   >
   > So this plan uses `named`. Approving this plan approves that
   > expression. Step 1 still re-runs the check, because the lock may move
   > before implementation.

2. **Precondition: #138 and #135 have merged.** Run
   `gh pr list --state merged --search 138`, the same for 135, and
   `git log origin/main --oneline | grep -E '#13[58]'`. If either has not
   merged, **stop and report. Do not implement.** Otherwise run
   `git rebase origin/main`, repeat step 0 on the new base (record the new
   count), and re-check every `file:line` in this plan
   → verify by a clean rebase (resolved as in "Landing order"), a green
   baseline, and line numbers confirmed or corrected in this file, in the
   same commit as the code.

3. **Tests first, and see them fail on `main`.** Add the tests listed under
   Tests before any `src/` change. Run both runners → verify by every new test
   failing, and no existing test changing. Tests 4, 14 and 15 describe
   today's behaviour, so each of them also patches
   `feedback.PiperWorker.start` and asserts on its calls. On `main` that
   patch raises `AttributeError`, which is how they fail there.

4. **`src/omarchy_voice/piper_worker.py` (new): decision 1.** About 30
   lines, standard library plus `piper`, and a module docstring saying it
   runs on piper's interpreter and why it imports nothing of ours
   → verify by tests 1, 2, 12 and 7 passing (real worker, fake `piper`).

5. **`src/omarchy_voice/feedback.py`: `PiperWorker`, decisions 2, 4, 8, 9,
   12, 18, 19.** Put the class after `piper_rate` (`:58-70`) and before
   `class Feedback` (`:73`). The worker path is
   `Path(__file__).with_name("piper_worker.py")`. Add `select` and `struct`
   to the imports (`:13-20`)
   → verify by tests 1, 2, 7, 12 and 13 passing.

6. **`src/omarchy_voice/feedback.py`: `Feedback`, decisions 3, 5, 10, 11,
   13, 20, 21.** New state in `__init__` (`:74-88`), `start_piper()`,
   `stop_piper()`, and the new top of `_speak_piper` (`:170-206`). The
   per-sentence code and espeak-ng below it are not edited
   → verify by tests 3-6, 10, 11 and 14-16 passing, and by
   `VoiceSelectionTests`, `SampleRateTests` and
   `SpeakingIntoAnOpenMicTests` (`tests/test_feedback.py:35`, `:61`, `:81`)
   passing unchanged.

7. **`src/omarchy_voice/local_engine.py`: decisions 4 and 5.** Beside
   `hearing` (`:953-955`), when `not config.tts_command and not
   elevenlabs.ready(config)`, add
   `piper = asyncio.ensure_future(asyncio.to_thread(self.feedback.start_piper))`.
   After `self.server = await hearing` (`:965`), await it, and log
   `start   piper resident` or `start   piper per sentence` next to the
   whisper line (`:968-969`). In the `finally` (`:978-990`), after the
   `server.stop` (`:986-988`), await the eager start if it is still running
   and then call `await asyncio.to_thread(self.feedback.stop_piper)`
   → verify by tests 8 and 9 passing, and `WatchAnnounceTests`
   (`tests/test_local_engine.py:1882`), which drives the real `run()`,
   passing unchanged.

8. **`nix/package.nix`: decision 14.** Add a `let` binding with the
   expression that step 1 chose (`named`, unless step 1 says otherwise) and
   one wrapper line after `OMARCHY_VOICE_PIPER_MODEL` (`:101-102`):
   `--set-default OMARCHY_VOICE_PIPER_PYTHON ${piperPython}`. `lib` and
   `python3Packages` are already arguments (`nix/package.nix:1-3`). No new
   argument and no new package
   → verify by `nix build` and then
   `grep OMARCHY_VOICE_PIPER_PYTHON result/bin/omarchy-voice`, and by that
   path running `-c 'import piper'`. Also check that
   `result/lib/python3.*/site-packages/omarchy_voice/piper_worker.py` exists,
   and that `nix path-info -r result | grep -c piper-tts` is unchanged from
   `be27af4`'s build.

9. **`README.md`, "Her local voice" (`:327-351`): one paragraph.** Piper is
   kept loaded where it speaks. If there is no ElevenLabs key it stays
   loaded from start-up, at about 135-190 MB. If ElevenLabs is set up, it
   loads at the first fallback. The `start   piper …` and
   `warn    tts: piper resident failed …` lines are what to look for
   → verify by reading it.

10. **Mutation checks** (listed under Tests). Apply each one, run the named
    test, see it turn red, and revert → verify by `git diff` being empty
    afterwards, apart from the intended change.

11. **Both runners, the flake check and the process check** (see Tests)
    → verify by all green, and `pgrep -f piper_worker` being empty.

12. **Hand the owner-only gate to the lead** (see the gate section below).
    Do not merge before the owner has recorded the result on #136.

## Tests

All of them run with `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`. No
real piper, no audio, and no process is left running (each test that spawns
the worker calls `stop()` in `addCleanup`, and asserts
`proc.poll() is not None`).

**The fake piper.** A temp dir holding `piper/__init__.py`, whose
`PiperVoice.load(path)` returns an object whose `synthesize(text)` yields
objects with `audio_int16_bytes`. It yields `text.encode()` as one chunk,
two chunks for a text starting `two:`, and it sleeps 60 s for `hang`. The
real worker is run as `[sys.executable, "-P", piper_worker.py, model]` with
`PYTHONPATH=<temp dir>`, and `OMARCHY_VOICE_PIPER_PYTHON=sys.executable`.
**Fake `pw-cat`.** `subprocess.Popen` is patched with a side effect that
passes the worker's argv to the real `Popen` and returns a recorder object
for `pw-cat` and for the per-sentence `piper`. `shutil.which` is patched to
find `pw-cat` and `piper`.

In `tests/test_feedback.py`, class `PiperWorkerProtocolTests` (real worker,
fake piper), after `SpeakingIntoAnOpenMicTests` (`:81`):

1. `test_worker_frames_each_sentence`: two lines in give ready, chunk(s), 0,
   chunk(s), 0, and the bytes match the fake's output, including the
   two-chunk sentence. (decision 1)
2. `test_worker_exits_on_eof`: closing its stdin ends it with code 0 within
   2 s. This is the "dies with the daemon" guarantee. (decision 4)
7. `test_hung_worker_times_out`: `hang`, with `PIPER_READ_TIMEOUT` patched
   to 0.2 s. The per-sentence path speaks the sentence, and the worker
   process has exited. (decisions 9, 10)
12. `test_a_newline_does_not_split_a_sentence`: `speak("one\ntwo")`, then
    `speak("three")`. Each call gets exactly one end marker, and the second
    call's audio is `b"three"`, not left-over bytes. (decision 8)

In `tests/test_feedback.py`, class `ResidentPiperTests` (a fake
`PiperWorker`, and a fake `Popen` for the per-sentence path):

3. `test_resident_piper_is_used_for_the_second_sentence`: env set, the model
   present. Two `_speak_piper` calls give one `start`, two `speak` calls,
   and no per-sentence `piper` Popen. (decision 5, lazy)
4. `test_no_python_means_no_worker`: `OMARCHY_VOICE_PIPER_PYTHON` unset. The
   per-sentence path runs, and `PiperWorker.start` is never called.
   (decision 5)
5. `test_dead_worker_falls_back_before_any_audio`: the worker gives EOF
   before its first chunk. The per-sentence piper speaks the sentence,
   `_piper_failed` is set, exactly one warn line is logged, and the next
   sentence does not call `start` again. (decisions 3, 10)
6. `test_partly_heard_sentence_is_not_repeated`: one chunk, then EOF. No
   per-sentence piper for that sentence, and one warn line. (decision 10)
10. `test_sentence_never_logged`: across the cases in 5, 6 and 7, the
    sentence text is in no log line. (decision 11)
11. `test_resident_piper_adds_no_synth_span`: with `feedback.trace` set to a
    `trace.Trace`, two resident sentences add no span at all (so no SYNTH),
    and no span name contains the text. (decisions 7, 11)
13. `test_two_sentences_never_share_the_pipe`: two threads call
    `_speak_now` at once, and a fake worker that raises if it is re-entered
    sees the calls one after the other. (decision 12)
14. `test_no_model_means_espeak_and_no_worker`: env set, no model. espeak-ng
    runs, and `PiperWorker.start` is never called. (decision 6)
15. `test_building_a_feedback_starts_nothing`: env set, the model present.
    `Feedback(config)`, `state()` and `notify()` never call
    `PiperWorker.start`. (decision 5, the `cli.py:206` and
    `local_engine.py:1044` case)
16. `test_worker_that_fails_to_start_is_not_retried`: `start` returns None.
    That sentence goes per sentence, one warn line is logged, and the next
    sentence does not call `start`. Also, with the real worker and a fake
    piper whose `load` raises, `PiperWorker.start` returns None and the
    process has exited. (decisions 2, 3, 20)

In `tests/test_local_engine.py`, class
`ResidentPiperWiringTests(EngineTestCase)`, placed directly before
`class FailureTests` (`:1588`). It drives the real `run()` in the shape of
`test_a_finished_watch_is_announced` (`:1920-1944`), with
`Feedback.start_piper` and `Feedback.stop_piper` patched:

8. `test_worker_starts_at_daemon_start_only_when_piper_is_first`: with
   `elevenlabs.ready` true, no `start_piper` at daemon start. With it false
   and no `tts_command`, exactly one call, and the `start   piper …` line.
   (decision 5, eager)
9. `test_engine_stops_the_worker`: `stop_piper` is called once on a clean
   exit (`_stop` set), and once on an error exit (the brain's `start`
   raises). (decision 4)

Unchanged, and must pass: `VoiceSelectionTests`, `SampleRateTests`,
`SpeakingIntoAnOpenMicTests`, and every #114, #86 and #79 test
(`HerVoiceGatesTheMicTests` `:1230`, `SpokenConsentTests` `:758`,
`SpeakingSideTimingTests` `:1390`, `tests/test_trace.py`).

Count: 1084 + 16 = **1100** at `be27af4`, or the post-rebase baseline + 16.

**Mutation checks, one per decision.** Each must turn the named test red,
and is then reverted:

| Decision | Mutation | Red |
| --- | --- | --- |
| 1 framing | the worker writes the raw chunk with no length prefix | 1 |
| 2 start fails safe | `start` returns the worker without waiting for the ready marker | 16 (the real worker whose `load` raises) |
| 3 no restart | clear `_piper_failed` after the fallback | 5, 16 |
| 4 dies on EOF | the worker does `continue` on `b""` | 2 |
| 4 engine stops it | remove `stop_piper` from the `finally` | 9 |
| 5 lazy | `_speak_piper` always starts a new worker | 3 |
| 5 eager only when first | start regardless of the voice chain | 8 |
| 5 env unset | ignore an unset `OMARCHY_VOICE_PIPER_PYTHON` (use `sys.executable`) | 4 |
| 5 no start on construction | start the worker in `__init__` | 15 |
| 6 espeak-ng | try the worker when there is no model | 14 |
| 7 no SYNTH | wrap the resident speak in `trace.mark(trace_mod.SYNTH)` | 11 |
| 8 newlines | send `text` without replacing `\n` | 12 |
| 9 deadline | drop the `select` deadline (block on `os.read`) | 7 (with a 5 s test timeout) |
| 10 nothing heard | no fallback when the worker dies before any bytes | 5 |
| 10 partly heard | fall back even after bytes were played | 6 |
| 11 no text in log | put `text` into the warn line | 10 |
| 12 lock | remove the lock | 13 |
| 14 packaging | remove the wrapper line | step 8's `grep` (build check) |
| 18 `-P` | drop `-P` and add a `trace.py` to the fake piper's dir that raises on import, imported by the fake `piper` | 1 |

**Runners.**
- `nix develop -c python3 -m pytest tests -q` → all pass, 1100.
- `nix develop -c python3 -m unittest discover -s tests` → the same.
- `nix flake check --no-write-lock-file` → passes. It builds the package, so
  it builds the worker's interpreter (step 8).
- `nix build`, then step 8's three checks on `result`.
- `pgrep -f piper_worker` → empty after all of the above.

## Owner-only gate before merge (this plays audio)

For the lead to hand to the owner, as it is. Nothing here is run by an
agent.

- [ ] Build both: `main` (`be27af4`, or the post-#135 `main`) and this
      branch, each with `nix build -o result-<name>`.
- [ ] A temp `XDG_CONFIG_HOME` whose `config.toml` has
      `[elevenlabs] enabled = false`, no `tts_command`, and
      `trace_timings = true`. The `start` line must read `voice=piper`.
- [ ] **Fixed sentences, no brain.** A scratch driver calls
      `Feedback(config)._speak_now(s)` for the same 10 fixed sentences
      (from 1 word to about 25 words), 3 rounds on each build, and records
      the wall time per sentence. **Pass: the branch's median is at least
      1.2 s lower** (about 1.4 s expected), and the gap does not grow with
      sentence length.
- [ ] **The daemon, traced.** On each build, start the daemon and wait for
      `start   brain ready` (and on the branch, `start   piper resident`).
      Run the same 5 commands with `omarchy-voice listen say ...`, then
      `tools/timing_report.py`. **Pass: the median `speak=` per sentence is
      about 1.4 s lower on the branch.** `first-audio=` is **not** compared
      (decision 7).
- [ ] After each run, and after a `kill -9` of the daemon,
      `pgrep -f piper_worker` is empty.
- [ ] The numbers are recorded as a comment on #136. **Below 1.2 s, it is not
      merged.**

## Rollback

- Code: `git revert` the merge commit. No data, config or state format
  changes, so nothing needs migrating.
- At runtime, with no rebuild: set `OMARCHY_VOICE_PIPER_PYTHON=` (empty) in
  the user unit's environment. `--set-default` does not override it, and an
  empty value means no worker (decision 5), which is today's behaviour. Then
  `systemctl --user restart` the daemon.
- A worker that misbehaves already rolls itself back to the per-sentence
  path for the rest of the daemon's life (decision 3).

## Deviation found while implementing (2026-09-25)

- **Step 2 was not run as written.** On 2026-09-25 neither #138 nor #135 had
  merged (`gh pr list` found no PR for either, and `origin/main` was still
  `be27af4`). Following the team's flow, the lead told the implementer to
  build on `be27af4` and said the lead will rebase this branch onto #138 and
  #135 before the merge. So the `file:line` references above are still the
  `be27af4` ones. The rebase, the conflict resolution in "Landing order" and
  the new baseline count are the lead's job at merge time. The count here is
  1084 + 16 = 1100.
- **Mutation 18 (`-P`).** The check is built into the fake `piper`, not
  added by the mutation. The fake runs `import trace` and raises if the
  module it gets has `SYNTH`, which means our `trace.py` shadowed the
  standard library's. So the mutation is only "drop `-P`", and test 1 is red.
- **Step 8: the `named` expression is wrapped by `makeWrapper`, not
  `writeShellScript`.** Step 1 passed as flagged: `first` did not evaluate,
  `second` built but could not import piper, and `named` imported piper from
  `/nix/store/58ji2wj2…-piper-tts-1.8.0`. But `writeShellScript` is not an
  argument of `nix/package.nix`, and step 8 says "no new argument". So
  `postFixup` runs `makeWrapper <python3Packages.python.interpreter>
  $out/libexec/piper-python --set PYTHONPATH <named's lib.makeSearchPath
  expression, unchanged>`. `makeWrapper` is already a native build input. The
  wrapper is in our own output, so there is no new derivation, and
  `OMARCHY_VOICE_PIPER_PYTHON` defaults to `$out/libexec/piper-python`.
  Checked: `nix path-info -r result | grep -c piper-tts` is 1 on `be27af4`
  and on this branch, with the same path, and `piper-python -c 'import piper'`
  imports it from there. The shipped worker was also run once with the real
  voice, with its output discarded: ready in 1.44 s, one chunk per sentence
  (0.06 s and 0.19 s), and exit 0 on EOF.
