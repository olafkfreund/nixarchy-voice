---
status: draft
issue: 72
intent: intent/2026-09-24-72-listen-faster.md
---

# Spec: stop spending seconds between the user finishing and the model starting

## The intent's three open questions, answered

The intent was approved without answers to its three questions, so each is
decided here with the reasoning shown. Any of them can be rejected at this
gate.

### Q1: how is the end of a sentence detected? A shorter fixed hold now; a VAD is its own issue.

The hold drops from 1.5 s to **0.8 s**, as a new local-engine setting. A
learned end-of-turn detector (Silero, which ships with whisper-cpp 1.9.2, or
smart-turn) is a bigger change with its own model, and goes to a new issue.
This change makes the trace able to show whether it is worth it.

This cannot be done by changing `silence_hold_seconds`, which is what the
intent's option (a) assumed. That setting is shared: the realtime engine uses
it for its upload gate (`realtime.py:837-858`), where it must stay
"comfortably longer than the pause semantic_vad needs to call a turn finished"
(`config.py:303-305`). Lowered, OpenAI's turn detection would never hear the
end of a turn. So the local engine gets its own key, `end_of_speech_seconds`,
and `silence_hold_seconds` keeps its 1.5 s and its meaning.

### Q2: what keeps whisper resident? The daemon owns `whisper-server`, behind a secret path.

The daemon starts `whisper-server` from the `whisper-cpp` package it already
depends on, when the local session starts. It stops it when the session ends.
No new unit, and no change to the Home Manager module's service shape.

`whisper-server` has no authentication, and a loopback port can be reached by
any local process, **including a web page in the browser**. A page can POST a
form to `127.0.0.1` without being allowed to read the reply. It could not read
a transcript, but it could hit `/load`, which loads a model from any path.
Tested here with `--request-path /<128-bit random hex>`:

```
/                                             404
/inference                                    404
/load                                         404
/a4346416cc54ab268bd6dd3ec6b95456/inference   200
/a4346416cc54ab268bd6dd3ec6b95456/load        400   (reachable only with the secret)
```

So the server is bound to `127.0.0.1` (its default), on a free port chosen at
start, with a fresh random request path every time it starts.

Found while testing: this machine already runs a **system** `whisper-server`
(`whisper-server.service`, `/var/lib/whisper/ggml-base.en.bin`) on
**`0.0.0.0:9300`**, which is reachable from the network with no path secret.
It is not this repo's, and it is not reused (see Alternatives). Its exposure
is worth a look on its own, outside this issue.

### Q3: is speaking in scope? No.

This covers listening only. A separate issue will be opened for speaking
(ElevenLabs fetching the whole clip first, Piper per sentence, the 0.35 s
echo tail), to be measured once this change's trace can see a whole turn.

## Design

### 1. The vocabulary prompt

`listen_local.vocabulary(config) -> str` builds one short comma-separated line
from:

- the configured `wake_word`;
- the product's own names: `Claude`, `Claude Code`, `Hyprland`, `Omarchy`,
  `workspace`;
- the coding-agent names in `capabilities.CODING_AGENTS`
  (`capabilities.py:785-794`), which change when that table changes;
- a new optional `whisper_vocabulary` string in config, for anything the user
  keeps seeing misheard.

Installed app names are left out until #70 gives a trustworthy index. The
current 28-entry list is mostly Steam runtimes. The prompt is capped at about
200 characters, because whisper's prompt window is small and a long prompt
biases decoding.

It is passed as `--prompt` to `whisper-cli` and as the `prompt` form field to
the server. Tested against non-speech audio, the prompt is not echoed back.
Silence, noise and a 100 Hz hum produce the same kind of junk with or without
it ("you", "(music)", "[…]"), and `clean()` (`listen_local.py:207`) already
rejects every one of those.

### 2. The resident transcriber

In `listen_local.py`:

- `class Server`: `start(config)` picks a free port (bind port 0, read it,
  close), generates `secrets.token_hex(16)`, and runs
  `whisper-server -m <model> --host 127.0.0.1 --port <p> --request-path /<token>
  -l en -nt`. It waits up to 10 s for a TCP connect to succeed. `transcribe(pcm,
  prompt)` wraps the PCM in a WAV in memory and POSTs multipart form data
  (built with the standard library; no new dependency) to
  `/<token>/inference`, with `response_format=text`, `prompt`, and a 10 s
  timeout. `stop()` terminates it, or kills it after 2 s. `alive` means the
  process is still running.
- `transcribe(pcm, config, server=None)` keeps its checks: the
  minimum-length check, `clean()`, `Unavailable`. With a live server it uses
  the server. **Any** server failure (dead process, refused connection,
  timeout, non-200) falls back to `whisper-cli` for that utterance, logs
  one line, and marks the server as gone for the rest of the session. Local
  listening never goes deaf because a helper died.
- `LocalSession` starts the server alongside `brain.start()`, in parallel so
  the 6.5 s warm-up hides it. It stops the server in the `finally` that
  already stops the brain (`local_engine.py:571-576`). If it fails to start,
  the session runs on `whisper-cli`, exactly as today.

`omarchy-voice ask` (`cli.py:51-61`) is one utterance per process, so it keeps
`whisper-cli`, and gains the vocabulary prompt.

### 3. The end of a sentence

A new `end_of_speech_seconds: float = 0.8` in `Config`, documented in
`share/config.example.toml`. `LocalSession._turn` passes it as `hang`
instead of `silence_hold_seconds` (`local_engine.py:260-261`). The wake path
uses it too, instead of `DEFAULT_HANG_SECONDS` (`local_engine.py:314-315`),
so one breath of "Oma, …" ends as quickly as an instruction does.

If a pause mid-sentence cuts the wake recording off after "Oma", nothing is
lost: the remainder is empty, so listening switches on exactly as today, and
the user's next words are picked up by `_turn`.

### 4. "Oma, <instruction>" runs the instruction

In `_wake_turn` (`local_engine.py:305-331`), when the wake word is heard, the
text after it is taken with a new `listen_local.after_wake_word(text, wake)
-> str`, which returns `clean(...)` of that remainder. It is acted on only
when all three of these hold:

1. **The wake word is one of the first two words.** "Oma, close it" and "Hey
   Oma, close it" run it. "I told Oma to close everything", overheard, only
   switches listening on, as today. That keeps the constraint that the wake word
   must not get more eager.
2. **The recording ended on silence, not on the cap.** The `_record` call is
   timed. A recording that ends on silence returns before `wake_max_seconds`,
   and one cut off by the cap returns after it. A capped recording may have
   lost the end of the sentence ("close all the windows except…"), so it only
   switches listening on.
3. **The remainder survives `clean()`.** "Oma?" leaves nothing, so it only
   switches listening on.

When all three hold, listening switches on and the remainder goes to
`_answer`, exactly as a `_turn` transcript would.

`wake_max_seconds` rises from 4.0 to **8.0**, because the cap counts from the
moment the recorder opens, not from the first word. At 4 s, "Oma" plus an
instruction often did not fit. With the resident server, transcribing 8 s of
room noise costs about 0.1 s.

### 5. The trace sees listening

Two phases are added to `trace.py`: `ENDPOINT = "endpoint"` and
`TRANSCRIBE = "transcribe"`. Both `_turn` and the wake path build the `Trace`
**when recording stops**, with `started` backdated by the hold, and add a
closed `endpoint` span for that hold. The hold is `hang` plus at most one 50
ms frame, since the recorder stops once the hold has passed. Transcription is
wrapped in a `transcribe` span. `_answer` takes the `Trace` as an optional
argument instead of making its own. A typed `say` still makes its own, as now.
The `TIMING` line then covers the user finishing through the last sentence.

## Alternatives rejected

- **Lower `silence_hold_seconds`.** It is shared with the realtime engine's
  upload gate, as shown in Q1.
- **Reuse the system `whisper-server` on :9300.** It is not this repo's to
  depend on: another config owns its lifecycle, model and threads. It is bound
  to `0.0.0.0` with no path secret, and pointing the assistant's audio at a
  network-exposed service contradicts "nothing leaves the machine" in
  spirit, even over loopback.
- **A systemd user unit from the Home Manager module.** It needs a way to hand
  the path secret to the daemon, adds packaging for a helper only the daemon
  uses, and splits one lifecycle in two.
- **An in-process binding (`pywhispercpp` or similar).** A new Python
  dependency and native build, to save only the few milliseconds of a
  loopback HTTP request over a resident server.
- **Keep the model in the page cache (`vmtouch`, `mlock`).** It fixes only the
  cold 1.37 s. The warm 0.32 s stays, where the server takes 0.05 s.
- **Change `record_utterance` to return why it stopped.** Five callers
  (`local_engine`, `cli`, `realtime`, `tools/bench_local.py`, the tests), to
  learn what timing the call already tells us.
- **Silero VAD / smart-turn / streaming STT.** A bigger change, as decided in
  Q1, and a follow-up issue.

## Risks

- **0.8 s cuts off someone who pauses mid-instruction.** "Open Firefox …
  on workspace two" can become "open Firefox". That is recoverable, and a
  consequential action is still held by the confirm gate. It is configurable,
  and the log's `heard` lines show cut-off sentences. The trace shows what the
  0.7 s bought.
- **The path secret is on the server's command line**, and `/proc/<pid>/cmdline`
  can be read by other local users unless `hidepid` is set. The threat this
  defends against, a web page posting to loopback, cannot read `/proc`. A
  second local user who can is a residual risk, stated rather than engineered
  around.
- **GPU memory.** Here the server loads onto Vulkan (`Vulkan0 total size =
  147.37 MB`), which is why it takes 0.05 s. It holds that for the session's
  life. On a host where it cannot start, the session falls back to
  `whisper-cli`.
- **Port race.** Between choosing the free port and the server binding it,
  another process could take it. The server then fails its readiness check
  and the session uses `whisper-cli`.
- **Overheard "Oma, …" now acts.** Only with the wake word at the start of the
  utterance, and every action still passes the policy gate.
- **Hosts:** this machine's user service. If the host flake pins the package,
  a rebuild picks it up. No NixOS module or system change.

## Verification

- Unit (`pytest tests -q` in `nix develop`):
  - `vocabulary()` includes the wake word, `Claude`, the coding-agent
    names and `whisper_vocabulary`, and stays under the cap.
  - `whisper-cli` is called with `--prompt <vocabulary>` (faked `subprocess.run`).
  - `Server` against a stand-in HTTP server (`http.server` on a thread)
    POSTs to `/<token>/inference` with the `prompt` field. A request to a
    closed port falls back to `whisper-cli`, logs once, and uses
    `whisper-cli` for the next utterance too.
  - `after_wake_word`: "Oma, close it" gives "close it"; "Hey Oma, close
    it" gives "close it"; "I told Oma to close it" gives ""; "Oma?"
    gives "".
  - `_wake_turn` with a faked recorder and transcriber: remainder and a
    silence ending mean `_answer("close it")`; a capped recording means
    listening switches on and `_answer` is not called.
  - `_turn` passes `end_of_speech_seconds` as the hold. The realtime gate
    still reads `silence_hold_seconds`, which still defaults to 1.5.
  - A traced `_turn` has `endpoint` and `transcribe` spans, and its `started`
    is before the recording ended.
- `nix flake check --no-write-lock-file` passes.
- Live, on this machine, with `trace_timings = true` and the service on this
  branch:
  - `ss -ltnp` shows the daemon's `whisper-server` on `127.0.0.1` only, and
    `curl -X POST 127.0.0.1:<port>/inference` returns 404.
  - Ten voice turns: `TIMING` lines show `endpoint` ≈ 0.8 s and `transcribe` ≈
    0.05-0.1 s, against today's 1.5 s and 0.32/1.37 s.
  - "Start a new Claude session" is logged as `heard 'Start a new Claude
    session…'`.
  - "Oma, what time is it" in one breath is answered without a second
    utterance.
  - `kill` the daemon's `whisper-server` mid-session: the next utterance is
    still transcribed through `whisper-cli`, with one `transcribe` fallback line
    in the log.
