---
status: approved
issue: 128
spec: spec/2026-09-24-128-wake-capped-echo-tail.md
---

# Plan: a wake capture's cap counts only the time the microphone was open

Closes #128. Base: `main` `50dcf99` (1085 tests collected, 104 of them in
`tests/test_local_engine.py`, counted on this branch at `a9545ef`, which adds
only docs). Every `file:line` below was checked against `50dcf99`. Only
`src/omarchy_voice/local_engine.py` and `tests/test_local_engine.py` change.
`listen_local.py`, `config.py`, `claude_backend.py` and `flake.nix` do not.

## Approved decisions, carried over from the spec

1. **`_record` stamps the open.** `self._opened = time.monotonic()` goes on
   the line directly before `self._mic_shut.clear()` (`local_engine.py:405`).
   That is after #114's echo-tail loop (`:377-388`) and the `_onset` /
   `_last_loud` resets (`:390-391`). It is the point the engine already
   treats as "the capture is open": `_mic_shut` is cleared there and set
   again in the `finally` (`:416`). No `await` is added, so nothing can run
   between the stamp and the clear.
2. **`_wake_turn` measures from the stamp.** Delete the local
   `opened = time.monotonic()` (`:478`). `capped` (`:482`) becomes
   `time.monotonic() - self._opened >= self.config.wake_max_seconds`. The #72
   comment above it (`:480-481`) gains "from when the microphone opened, not
   from when the wait for her began (#128)". Nothing else in `_wake_turn`
   changes.
3. **`_opened` is session state, initialised in `__init__`.** `self._opened =
   0.0` beside `_onset` and `_last_loud` (`:244-249`), with the comment "when
   the latest capture opened, after her echo tail: a wake capture's cap is
   timed from it (#128)". It is set on every capture, `_turn`'s included;
   `_turn` never reads it. `_record` is only entered from the listen loop,
   one capture at a time (`:421`, `:479`), so it cannot go stale between
   `_record` returning and `_wake_turn` reading it.
4. **Why the stamp cannot miss a real cap.** `record_utterance` starts its
   own clock after `Popen` (`listen_local.py:310`) and stops on
   `now - started > max_seconds` (`:332`). `_opened` is taken before that
   thread starts, so `now - _opened >= now - started`. A capture that hit
   its cap is always judged capped. #72 is not weakened.
5. **Teardown stays inside `capped`.** `pw-record`'s terminate-and-reap (up to
   2 s, `listen_local.py:334-339`) is still counted. It can only push a
   capture that ended just under the cap over it, which errs towards "only
   wake", the safe side of #72. Taking it out would need a second stamp from
   the recorder thread. Not done.
6. **Rejected: (b) the first level callback as the start.** It is later than
   the open by `pw-record`'s startup, so it undercounts, and a recorder that
   never reports a level would leave it unset. `_onset` stays #86's.
7. **Rejected: (c) `record_utterance` reports its cap.** It changes the
   return type shared by `_turn`, `_wake_turn` and every recorder fake.
   `record_utterance` and its signature do not change.
8. **Elapsed time only; `_last_loud` is not read by `capped`.** It marks the
   last loud frame (#80), not the open, and adds no correct "not capped"
   case. It stays read only by `_hear`'s ENDPOINT span and reset only in
   `_record`.
9. **Tests: both cases, a stepped cap test, and `:1687` stays.** A short case
   (only the echo tail) and a long case (she is still speaking when the wake
   capture is asked for) are tested, plus a stepped-clock capture that
   really runs to its cap after a reply and still only wakes.
   `test_a_recording_cut_off_by_the_cap_only_wakes`
   (`tests/test_local_engine.py:1687`, `wake_max_seconds=0`) is not moved:
   `now - _opened >= 0` is always true, so it still guards #72.
10. **Scope.** No new config knob, no change to `record_utterance`, `_turn`,
    `_hear`, `_say` or `_listen_loop`. Also rejected: subtracting the timed
    tail wait from a local `opened` (two places that know about the loop),
    and moving the tail wait out of `_record` into its callers (undoes #114).
11. **Invariants.** #114: the tail loop, `ECHO_TAIL_SECONDS`, `_ShutForHer`,
    the `None` return and the `_mic_shut` pair behave as now. #86: `_onset`
    and `_heard_at()` unchanged. #80: `_last_loud` unchanged. #120: the
    listen loop's recovery is untouched and no new exception path is added.
    #72: a capture that ran to `wake_max_seconds` is never acted on.

## Landing order

**#128 lands before #139.** Both edit `src/omarchy_voice/local_engine.py`
and `tests/test_local_engine.py`. At `50dcf99` #139
(`fix/139-tools-while-speaking`, spec approved at `b5349ce`) has no code.
Its spec changes, in `local_engine.py`, `make_brain`'s `LocalBrain`
(`:197-201`, sets `speech_first` from `config.barge_in`) and `_say`'s
docstring (`:327-333`); the rest of it is `claude_backend.py`:
`_pre_tool_use` (`:481-503`) and the stream reader `ClaudeBrain._turn` (the
`tool_use` id record), not `LocalSession._turn` or `_answer`. This plan edits
`__init__` (`:244-249`), `_record` (`:404-405`) and `_wake_turn`
(`:477-482`). No hunk is shared, so the overlap is positional only:
- `_say` (`:326`) sits below `__init__`, so #139's `_say` line numbers move
  by the lines this plan adds there (+3: two comment lines and the
  assignment). `LocalBrain` (`:197`) is above and does not move.
- In the tests, #139's engine test is a `SteppedRoomCase`
  (`tests/test_local_engine.py:1168`) like this plan's. This plan inserts its
  class between `HerVoiceGatesTheMicTests` and `class SteppedBrain`
  (`:1376`). If #139 inserts at the same spot, the rebase conflict is two
  adjacent new classes: keep both.
#139's plan re-checks its `file:line` references after #128 merges.

**Independent of the tools.py batch (#77, #83, #91, #129).** Checked on their
branches: #77's plan changes `tools.py`, `mcp_server.py`,
`claude_backend.py` and their tests and says `local_engine.py` does not
change; #83, #91 and #129 do not name `local_engine.py` or
`tests/test_local_engine.py` as changing. Any order among them works.

## Steps

0. **Baseline.** `git fetch origin`; `gh issue view 128` shows OPEN;
   `git branch --show-current` is `fix/128-wake-capped-echo-tail`. Then
   `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent;
   nix develop -c python3 -m pytest -q` and
   `nix develop -c python3 -m unittest discover -s tests`
   → verify by both passing with **1085** tests (104 in
   `tests/test_local_engine.py`). If `origin/main` has moved past `50dcf99`,
   rebase, repeat, and re-check every `file:line` here (correct them in this
   file in the same commit as the code).
1. **`tests/test_local_engine.py`: add `WakeCapTests(SteppedRoomCase)` and
   see it fail on `main`.** Insert it directly after
   `HerVoiceGatesTheMicTests` (ends `:1374`), before `class SteppedBrain`
   (`:1376`). No `src/` change yet. Reuse #114's `Room` (`:1086`) and
   `SteppedRoomCase` (`:1168`) unchanged.
   - Every test calls `self.stepped_sleep()` (`:1187`), never `no_tail()`:
     the tail wait must move the stepped clock, or it recomputes the same
     delay forever. It builds with `wake_word="oma"` and
     `end_of_speech_seconds=0.6` (pinned, not read from the default at
     `config.py:362`), sets `session.active = False` and
     `session._wake_ready = True`, and uses the Room script
     `("wait", "Oma, close the browser.")`, as
     `test_her_reply_does_not_wake_listening` (`:1314`) does. `await
     self.looping(session)`, then `await session._inject("hello")`: her reply
     shuts the `"wait"` capture, and the loop's next wake capture opens
     after it.
   - On the Room the wake capture lasts 0.5 s of speech, the 0.6 s hold and
     one frame: 1.10-1.15 s on the stepped clock (0.05 s frames).
   - Tests:
     1. `test_a_wake_right_after_her_echo_tail_is_acted_on`:
        `FakeBrain(["It is noon."])` (one sentence, so `FakeBrain`'s
        `spoken_first` wait at `:88-94` is never reached),
        `wake_max_seconds=1.3`. Wait on `self.until(lambda:
        len(brain.asked) >= 2, 30)`. Assert `brain.asked == ["hello",
        "close the browser."]`, `session.active`, and for
        `opened, voice_until = room.opens[1]`: `opened - voice_until >=
        TAIL - 1e-9` (the tail really was waited). On `main`: elapsed
        ≥ 0.35 + 1.10 > 1.3, capped, only wakes → **FAILS**.
     2. `test_a_wake_while_she_is_still_speaking_is_acted_on`:
        `SteppedBrain(self, ["One.", "Two.", "Three."])` (`:1376`, think 0:
        no yield between sentences), `wake_max_seconds=2.0`, below the
        reply's 3 s and above the capture. Same wait and `brain.asked`
        assertion. Also assert `len(room.opens) >= 2`, that
        `room.opens[1]`'s `voice_until` is finite, and `opened -
        voice_until >= TAIL - 1e-9` (#114 kept). On `main`: the wait
        through two or three sentences plus tail plus capture ≥ 2.0, capped
        → **FAILS**.
     3. `test_a_wake_capture_cut_by_the_cap_after_a_reply_only_wakes`:
        `FakeBrain(["It is noon."])`, `wake_max_seconds=0.28`, shorter than
        the user's 0.5 s. The Room stops on its cap after 6 frames
        (0.30 s), mid-word, and returns the line. Wait on
        `self.until(lambda: len(room.opens) >= 3, 30)` (the loop only
        reaches capture 3, a `_turn`, after `_wake_turn` has returned).
        Assert `session.active` and `brain.asked == ["hello"]`. Passes on
        `main` and after (#72). 0.28 rather than any value under 0.5 so that
        a stamp at the first frame (0.25 s elapsed) reads as uncapped: see
        M5.
   → verify by `nix develop -c python3 -m pytest -q tests/test_local_engine.py
   -k WakeCapTests` on unchanged `src/`: **1 and 2 FAIL** with
   `brain.asked == ["hello"]`; 3 passes. No hang (run under `timeout 300`).
   If test 2 fails instead because `room.opens[1]` shows a capture opened
   between sentences (its script line consumed by a shut capture), that is
   a #114 gap, not this bug: stop and report, do not widen `Room`.
2. **`local_engine.py` `__init__` (`:244-249`): add `self._opened = 0.0`**
   after `_last_loud`, with decision 3's comment
   → verify by the full file still passing (the field is unread so far).
3. **`local_engine.py` `_record` (`:404-405`): stamp the open.** Insert
   `self._opened = time.monotonic()` between `self.feedback.mic_open = True`
   and `self._mic_shut.clear()`
   → verify by `git diff` showing the one line, after the tail loop and
   with no `await` beside it.
4. **`local_engine.py` `_wake_turn` (`:477-482`): read the stamp.** Delete
   `opened = time.monotonic()` (`:478`); `capped` reads `self._opened`;
   extend the #72 comment (decision 2)
   → verify by `-k WakeCapTests` 3 passed, and `-k "OneBreath or WakeWord
   or HerVoiceGatesTheMic or Endpoint or SpokenConsent"` unchanged and
   green (`:1687` included).
5. **Mutation checks**, one per decision. Apply each alone, run
   `-k "WakeCapTests or OneBreath"` under `timeout 300`, then
   `git checkout -- src/`:

   | # | Decision | Mutation | Must fail |
   | --- | --- | --- | --- |
   | M1 | 1 | stamp `_opened` before the tail loop (top of `_record`) | 1, 2 |
   | M2 | 2 | restore the local `opened` in `_wake_turn` | 1, 2 |
   | M3 | 3 | stamp only `if wanted:` (listening on), so a wake capture keeps the `0.0` from `__init__` | 1, 2 |
   | M4 | 4, 5 | stamp `_opened` after `record_utterance` returns (teardown and capture both out) | 3 |
   | M5 | 6 | stamp `_opened` in `watch` on the first level callback | 3 |
   | M6 | 7 | none possible without code; verify `git diff --stat` shows `listen_local.py` and every recorder fake untouched | n/a (diff) |
   | M7 | 8 | `capped` timed from `self._last_loud` | 3 |
   | M8 | 9 | `capped = False` | 3 and `:1687` |
   | M9 | 10, 11 | read the diff: only `__init__`, `_record`, `_wake_turn` and the new test class change; `-k "HerVoiceGatesTheMic or Endpoint or SpokenConsent or MicrophoneGate"` green | n/a (diff + suites) |

   Decision 3's "0.0 in `__init__`" has no failing test (nothing reads it
   before the first capture); it is checked by reading the diff
   → verify by every mutation turning its tests red, and `git diff --stat`
   clean of `src/` after each revert.
6. **Full suites and the flake.** With
   `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`:
   `nix develop -c python3 -m pytest -q`,
   `nix develop -c python3 -m unittest discover -s tests`,
   `nix flake check --no-write-lock-file`
   → verify by all three passing with **1088** tests (107 in
   `tests/test_local_engine.py`).

## Tests

```
export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent
nix develop -c python3 -m pytest -q tests/test_local_engine.py -k WakeCapTests   # 3 passed (1, 2 fail on main)
nix develop -c python3 -m pytest -q tests/test_local_engine.py -k "OneBreath or WakeWord or HerVoiceGatesTheMic"   # unchanged, pass
nix develop -c python3 -m pytest -q                                              # 1088 passed
nix develop -c python3 -m unittest discover -s tests                             # Ran 1088, OK
nix flake check --no-write-lock-file                                             # passes
```

Fakes only: no microphone, no audio, no D-Bus. Every assertion waits on an
event or the stepped clock; none sleeps real time and then checks.

## Rollback

One code commit on `fix/128-wake-capped-echo-tail`. Before merge, drop the
branch. After merge, `git revert <sha>` restores the local `opened`. No
config, state or migration. Hosts pick it up on the next `omarchy-voice`
rebuild; p620 has `wake_word = ""` and sees no change either way.
