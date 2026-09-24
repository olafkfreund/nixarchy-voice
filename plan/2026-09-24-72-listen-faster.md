---
status: draft
issue: 72
spec: spec/2026-09-24-72-listen-faster.md
---

# Plan: stop spending seconds between the user finishing and the model starting

## Approved decisions, carried over from the spec

1. **A new `end_of_speech_seconds = 0.8`** for the local engine, used as the
   hold on both the instruction path and the wake path.
   `silence_hold_seconds` keeps 1.5 s and its meaning: it is the realtime
   engine's upload gate, which must outlast OpenAI's turn detection.
   Silero VAD, smart-turn and streaming STT are a follow-up issue.
2. **The daemon owns a `whisper-server`.** It binds `127.0.0.1`, on a free
   port chosen at start, with `--request-path /<secrets.token_hex(16)>` fresh
   every start. It starts in parallel with the Claude warm-up and stops in the
   same `finally` as the brain. Any server failure falls back to `whisper-cli`
   for that utterance, logs one line, and uses `whisper-cli` for the rest of
   the session. `omarchy-voice ask` stays on `whisper-cli`.
3. **The vocabulary prompt** is the wake word, `Claude`, `Claude Code`,
   `Hyprland`, `Omarchy`, `workspace`, the `CODING_AGENTS` names and an
   optional `whisper_vocabulary`, capped at about 200 characters. It goes to
   both `whisper-cli --prompt` and the server's `prompt` field. Installed
   apps wait for #70.
4. **"Oma, <instruction>" runs the instruction** only if all three hold: the
   wake word is one of the first two words; the recording ended before
   `wake_max_seconds` (timed around `_record`), meaning it ended on silence
   rather than the cap; and the remainder survives `clean()`. Otherwise
   listening switches on, as today. `wake_max_seconds` goes from 4.0 to 8.0.
5. **Trace phases `endpoint` and `transcribe`.** The `Trace` is built when
   recording stops, with `started` backdated by the hold, and is passed into
   `_answer`.
6. **Speaking is out of scope.** A follow-up issue is opened for it.
7. **The system `whisper-server` on `0.0.0.0:9300` is not reused or
   touched.** It is flagged to the user separately.

No Nix change: `whisperImpl` (whisper-cpp 1.9.2, Vulkan build) is already on
the wrapper's `PATH` (`nix/package.nix:79`), and it ships `whisper-server`.
This was checked against the running package.

## Steps

0. **Baseline.** `nix develop -c pytest tests -q` → record the pass count.
   This branch is off `main`, so it does not have #78.

1. **`src/omarchy_voice/config.py`.**
   - Add `end_of_speech_seconds: float = 0.8`, with a comment next to
     `silence_hold_seconds` saying which engine reads which, and why the
     realtime one must not be lowered.
   - Change `wake_max_seconds` from 4.0 to 8.0 (`config.py:376`), with a
     comment that the cap counts from when the recorder opens, not from the
     first word.
   - Add `whisper_vocabulary: str = ""`.
   → verify: `Config()` has the three defaults.

2. **`share/config.example.toml`.** Document `end_of_speech_seconds` near
   `silence_hold_seconds` (:92-99), change the commented `wake_max_seconds`
   to `8.0` (:127), and add a commented `whisper_vocabulary`. → verify: the
   config tests that parse the example still pass.

3. **`src/omarchy_voice/listen_local.py`.**
   - `vocabulary(config) -> str`: the parts from decision 3, de-duplicated,
     joined with `", "`, and cut at the last comma under 200 characters.
     `CODING_AGENTS` comes from `capabilities`, imported inside the function
     so this module's lightweight import stays that way (see the `_level`
     docstring for why that matters).
   - `after_wake_word(text, wake) -> str`: normalise the way
     `heard_wake_word` does. If a wake part is one of the first two
     normalised tokens, return `clean(<original text after that token>)`,
     keeping the original punctuation for whisper's sake. Otherwise return
     `""`.
   - `class Server`:
     - `start(config) -> Server | None` (a classmethod). If there is no model
       or no `whisper-server` on PATH, return `None`. Pick a port with
       `socket.bind(("127.0.0.1", 0))`, then close it. Generate the token.
       `Popen([whisper-server, -m, model, --host, 127.0.0.1, --port, p,
       --request-path, /token, -l, en, -nt], stdout/stderr=DEVNULL)`. Poll a
       TCP connect every 0.1 s for up to 10 s. On failure, terminate the
       process and return `None`.
     - `transcribe(pcm, prompt) -> str`: build a WAV in memory (`wave` into
       `io.BytesIO`). Build the multipart body by hand, with a
       `secrets.token_hex` boundary and the fields `file`,
       `response_format=text` and `prompt`. `urllib.request.urlopen(...,
       timeout=10)`. Raise on anything but 200.
     - `alive` (property): `proc.poll() is None and not self.failed`.
       `stop()`: terminate, then kill after 2 s.
     - A `ponytail:` comment on the class: the token sits on the command line
       (visible in `/proc` to other local users). The upgrade path is a Unix
       socket, if whisper-server gains one.
   - `transcribe(pcm, config, threads=4, server=None)`: after the
     minimum-length and model checks, `prompt = vocabulary(config)`. If
     `server and server.alive`: try `server.transcribe`, and on any
     `Exception`, set `server.failed = True` and fall through. The fallback is
     the existing `whisper-cli` call plus `"--prompt", prompt`. The return goes
     through `clean()` either way. The one fallback log line is written by the
     caller, which owns `feedback` (step 5).
   → verify by step 6.

4. **`src/omarchy_voice/trace.py`.** Add `ENDPOINT = "endpoint"` and
   `TRANSCRIBE = "transcribe"` beside the other phase constants (:36-41).
   → verify by step 6.

5. **`src/omarchy_voice/local_engine.py`.**
   - `__init__`: `self.server = None`.
   - `run()` (:552-560): replace `await self.brain.start()` with
     `_, self.server = await asyncio.gather(self.brain.start(),
     asyncio.to_thread(listen_local.Server.start, self.config))`. Log
     `start   whisper resident` or `start   whisper per utterance`.
     `finally` (:571-576): `if self.server: await
     asyncio.to_thread(self.server.stop)`.
   - A new `async def _hear(self, pcm, hold) -> tuple[str, Trace | None]`,
     used by both `_turn` and `_wake_turn`. It builds the backdated `Trace`
     when `trace_timings` is on, adding a closed `Span(ENDPOINT, "",
     now - hold, now)` and backdating `started` to match. It wraps the
     transcription in `mark(TRANSCRIBE)`. It calls `listen_local.transcribe(pcm,
     self.config, server=self.server)` via `to_thread`. If the server was
     alive before and is not after, it logs once: `warn    transcribe:
     whisper-server failed — using whisper-cli`. `Unavailable` propagates as
     today.
   - `_turn` (:257-285): the hold is `self.config.end_of_speech_seconds`
     (:261). Transcribe via `_hear`, then `await self._answer(text,
     trace)`.
   - `_wake_turn` (:305-331): the hold is `self.config.end_of_speech_seconds`
     (:315, replacing `DEFAULT_HANG_SECONDS`). Time the `_record` call. After
     `heard_wake_word`, compute `rest = listen_local.after_wake_word(text,
     wake)` and `capped = elapsed >= self.config.wake_max_seconds`.
     `_set_active(True)` as now. If `rest and not capped`, set
     `self._last_speech`, log `wake    heard {text!r} — acting on {rest!r}`,
     and `await self._answer(rest, trace)`.
   - `_answer(self, text, trace=None)` (:339): `task = trace or
     (trace_mod.Trace() if self.config.trace_timings else None)`. The rest is
     unchanged.
   → verify by step 6.

6. **Tests.**
   - `tests/test_listen_local.py`:
     (a) `vocabulary(Config(wake_word="oma", whisper_vocabulary="Vesktop"))`
     contains `oma`, `Claude`, `codex` and `Vesktop`, and is 200 characters
     or fewer.
     (b) `after_wake_word`: "Oma, close it." gives "close it."; "Hey Oma,
     close it" gives "close it"; "I told Oma to close it" gives ""; "Oma?"
     gives ""; "Omaha is nice" gives "".
     (c) `transcribe` with `subprocess.run` faked and no server: the argv
     includes `--prompt` followed by the vocabulary.
     (d) `Server` against a stand-in `http.server` on a thread, with the
     server object's port and token set by hand and `proc` a stub whose
     `poll()` returns None: the request goes to `/<token>/inference`, the
     body carries `prompt` and the WAV, and the result goes through `clean()`.
     (e) Server alive but the port closed: `transcribe` returns the
     whisper-cli result, `server.failed` is True, and a second call does not
     try the server.
     (f) `Server.start` with `shutil.which` returning None gives `None`.
   - `tests/test_local_engine.py` (the existing `EngineTestCase` harness;
     `transcribe` is already faked with `lambda *a, **k`):
     (g) The wake word and an instruction in one breath: with `self.heard =
     "Oma, close the browser."`, `brain.asked == ["close the browser."]` and
     the session is active.
     (h) Capped: the same, with `wake_max_seconds=0`, so any elapsed time
     counts as capped. The session is active and `brain.asked == []`.
     (i) Overheard: "I told Oma to close it" means active, `asked == []`.
     (j) `_turn` passes `end_of_speech_seconds` as the hold: `Ears` records
     its args, and `args[2] == 0.8`.
     (k) With `trace_timings=True`, the logged `TIMING` line contains
     `endpoint=` and `transcribe=`.
     (l) The existing `test_the_wake_word_starts_listening` ("Oma?") and
     `test_anything_else_heard_while_asleep_is_dropped` pass unchanged.
   - `tests/test_config.py`: `Config().silence_hold_seconds == 1.5` and
     `Config().end_of_speech_seconds == 0.8`, so the realtime gate keeps its
     hold.
   - Mutation check: make `after_wake_word` ignore the first-two-words rule →
     (b) and (i) fail. Then restore it.
   → verify: `nix develop -c pytest tests -q` equals the baseline plus the new
   tests, with no new failures.

7. **Whole check.** `nix flake check --no-write-lock-file` → it passes.

8. **Live check on this machine**, with the daemon running from this branch
   (a local `nix run .#omarchy-voice -- run`, or however the service is
   usually pointed at a branch; the PR says which) and `trace_timings = true`:
   - `ss -ltnp | grep whisper` shows the daemon's server on `127.0.0.1:<p>`
     only, and `curl -s -o /dev/null -w '%{http_code}' -X POST
     127.0.0.1:<p>/inference` returns `404`.
   - The user takes ten voice turns. The `TIMING` lines show `endpoint` ≈
     0.8 s and `transcribe` ≈ 0.05-0.1 s.
   - "Start a new Claude session" is logged with "Claude".
   - "Oma, what time is it" in one breath is answered with no second
     utterance.
   - `kill <server pid>`: the next utterance is still transcribed, and the log
     has one `whisper-server failed — using whisper-cli` line.
   The voice parts need the user's microphone. The server checks I run
   myself.

9. **Follow-ups and PR.** Open two issues: "speaking side latency
   (ElevenLabs whole-clip fetch, Piper per sentence, echo tail)", and
   "learned end-of-turn detection (Silero VAD / smart-turn)", each citing this
   PR's trace numbers. Push and open a PR that closes #72 and links the
   intent, spec and plan. The system `whisper-server` on `0.0.0.0:9300` is
   mentioned in the final report to the user, not in the PR.

## Tests

```
nix develop -c pytest tests -q                                   # baseline + new, 0 new failures
nix develop -c pytest tests/test_listen_local.py tests/test_local_engine.py tests/test_config.py -q
nix flake check --no-write-lock-file                             # CI parity
```

Plus the live checks in step 8.

## Rollback

It is a single squash-merged PR, with no Nix or schema change and no
persisted state. `git revert <merge>` restores `whisper-cli` per utterance,
the 1.5 s hold and wake-only activation. A user who set
`end_of_speech_seconds` or `whisper_vocabulary` in `config.toml` after this
landed must remove those keys, if the config loader refuses unknown keys. Step
1 checks how the loader treats unknown keys, and the PR states it. To turn it
off without reverting, set `end_of_speech_seconds = 1.5`. There is no switch
for the server: if it misbehaves, the fallback is already `whisper-cli`.
