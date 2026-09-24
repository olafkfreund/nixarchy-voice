---
status: approved
issue: 114
spec: spec/2026-09-24-114-every-voice-gates-the-mic.md
---

# Plan: her voice shuts the capture, and the capture waits out her echo

Closes #114. Base: `origin/main` `fc33ae2` (1075 tests collected, 83 of them
in `tests/test_local_engine.py`). Every `file:line` below was checked against
`fc33ae2`. Step 1 rebases onto #120, so re-check them after that rebase.
Only `src/omarchy_voice/local_engine.py` and `tests/test_local_engine.py`
change. `flake.nix`, `realtime.py`, `listen_local.py` and `feedback.py` do
not change.

## Approved decisions, carried over from the spec

1. **The capture closes on its next 50 ms frame.** When she is about to
   speak and a capture is open, the capture is abandoned on its next frame
   and its audio is thrown away. It does not wait for the capture to end.
   Waiting would cost up to `MAX_UTTERANCE_SECONDS` (15 s,
   `local_engine.py:51`) or `wake_max_seconds` (8 s, `config.py:430`), and
   it would still record her. The cost of closing is one frame plus stopping
   `pw-record` (`listen_local.py:280-285`). Anything the user was saying in
   that capture is lost, which is accepted: they are at the keyboard. The
   alternative "close it only if nothing has been heard yet (`_onset is
   None`)" was rejected. Key clicks set the onset, and it is two rules
   instead of one.
2. **The mechanism is in `_record`'s level callback.** `watch`
   (`local_engine.py:283-288`) already abandons the capture on toggle or
   stop. With `barge_in` off, it also abandons the capture when
   `self._voice_until == math.inf`. `_say` sets `inf` before it queues
   anything (`:252`). The callback runs for each frame before that frame is
   kept (`listen_local.py:266-271`). So no frame that could contain her
   voice is ever returned. This guarantee does not depend on timing. The
   state it reads is #86's clock. No new clock and no "hush" flag are added.
3. **`_say` waits for the capture to close before it queues.** An
   `asyncio.Event` called "mic shut" is cleared where `_record` sets
   `mic_open = True` (`:290`), and set in its `finally` (`:297-299`). With
   `barge_in` off, `_say` awaits it, bounded at 1 s, between setting `inf`
   and `self._speech.put` (`:252-254`). The bound is safe because decision 2
   does not depend on the wait. The wait only makes `feedback.mic_open`
   False while she plays. On the loop's own turns the mic is already shut,
   so the wait returns immediately.
4. **The echo tail moves to the start of `_record`, timed from
   `_voice_until`.** With `barge_in` off, the recorder first waits while
   `_voice_until` is `inf` (`await self._speech.join()`). It then waits until
   `_voice_until + ECHO_TAIL_SECONDS`, and re-checks in a loop, because
   another `_say` may land during the sleep. The tail is measured from when
   her voice ended, not from when the turn ended. A turn that said nothing
   waits for nothing. A turn that already slept its tail pays nothing more.
   `_drop_queued_speech` (`:258-268`) already makes `_voice_until` finite on
   mute, so a mute releases the wait. **The loop must not become a busy
   spin** (see the ambiguity note under Step 3).
5. **The short lines get the tail, with no extra code.** They all go through
   `_say`: NOT_CAUGHT (`:325`), the transcriber line (`:317`), #86's
   refusals (`:709`), the cancel line (`:716`) and "Done." (`:749`).
6. **`_answer`'s tail sleep is deleted** (`:501-506`). Decision 4 replaces
   it. Keeping both was rejected as dead weight that holds `_turn_lock`
   0.35 s longer than needed. The comment moves to the recorder's wait.
7. **The muted wake capture is covered by the same path.** `_wake_turn`
   opens the mic through the same `_record` (`:359`), so decisions 2 to 4
   apply to it without extra code. A reply that contains the wake word no
   longer switches listening on.
8. **Keybind confirm does not wait.** `_local_confirm` stays
   `_release(wait=False)` (`:724-726`, `:765`), and `_inject` still returns
   "sent" at once (`:674-675`). The control socket's 10 s limit
   (`:638-641`) is not touched. The spawned turn's own `_say` shuts the
   capture. `_release(wait=True)` and making `_inject` await `_typed` were
   both rejected: they hold the socket through a model turn, and they fix
   only two of the callers.
9. **A capture shut for her voice does not count as silence.** Such a
   capture returns `None`, not `b""`. `_turn` returns on `None` without
   calling `_idle_stop` (`:307-309`). `_wake_turn` already returns on a
   falsy `pcm` (`:363`). The toggle/stop abandon keeps returning `b""`.
   Without this, a typed line after 600 s of silence (`config.py:363`)
   switches listening off.
10. **#86's clock and checks A, B′ and D are only read.** `_voice_until`,
    `_said` and `_consent` (`:682-717`) are not written by the new code.
    Check A's 1.0 s guard is still longer than the 0.35 s tail.
    `SpokenConsentTests` must pass unchanged. One accepted difference: a
    spoken "confirm" that overlaps her voice is now discarded with the
    capture, so it is never refused with SOON. This fails closed.
11. **Nothing changes with `barge_in` on.** Every new branch is behind
    `not self.config.barge_in`. Also unchanged: `_inject`/`_typed`
    (`:663-680`), `_release` (`:728-767`), `_speech_loop` (`:222-242`) and the
    wait for announcements between captures (`:572-578`).
12. **Rejected, and not to be reintroduced:** taking `_turn_lock` in the
    listen loop (a capture would hold it for up to 15 s, and it misses the
    wake capture). Routing through `Feedback.speak`'s `mic_open` guard
    (`feedback.py:128-135`), which drops the sentence. A second recorder.
13. **#120 owns the barge-in test.** This plan does not edit
    `test_barge_in_lets_the_microphone_stay_open_while_she_talks` or
    `MicrophoneGateTests`. New tests do not copy their shape (a 30 s
    real-time `Event.wait` on a gated mouth, used as the assertion).

## Landing order

**After #120.** Branch `fix/120-barge-in-test-under-load` changes
`MicrophoneGateTests` and `_listen_loop` in `local_engine.py`, the same
two files this plan edits. At `fc33ae2` #120 has no PR and no remote branch.
Step 1 is a hard precondition.

## Steps

0. **Baseline.** `git fetch origin`, then confirm with `gh issue view 114`
   that #114 is OPEN. Then run
   `export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent;
   nix develop -c python3 -m pytest -q` and
   `nix develop -c python3 -m unittest discover -s tests`
   → verify by both runs passing. Record the counts: 1075 at `fc33ae2`, of
   which 83 are in `tests/test_local_engine.py`. Record any failure that
   already exists before continuing.
1. **Precondition: #120 has merged.** Run `gh pr list --state merged
   --search 120` and `git log origin/main --oneline | grep '#120'`. If #120
   has not merged, **stop and report. Do not implement.** If it has merged,
   run `git rebase origin/main`, repeat step 0 on the new base, and re-check
   every `file:line` in this plan against the new `origin/main`
   → verify by a clean rebase, a green baseline, and line numbers confirmed
   or corrected in this file (in the same commit as the code, per the
   workflow).
2. **`tests/test_local_engine.py`: add the room fakes and the new tests,
   and see them fail on `main`.** Add these before any `src/` change. Put
   them in one new class, `HerVoiceGatesTheMicTests(EngineTestCase)`,
   directly before `class FailureTests` (`:952` at `fc33ae2`). This keeps
   them away from `Mouth`/`Ears`/`MicrophoneGateTests`, which #120 edits.
   - *Stepped clock*, as in `SpokenConsentTests` (`:637-645`): patch
     `local_engine.time` with `self.now`. Every sentence the mouth plays
     moves it on by 1 s. Every room frame moves it on by 0.05 s.
   - *Room mouth*: records `(text, session.feedback.mic_open)`. While it is
     inside a sentence, it marks the room as "playing `text`". It returns
     once the room has read one frame during it, or at once if no capture is
     open. It has a 30 s bound for deadlocks only, as in `Mouth` (`:120`).
   - *Room recorder* (patched `listen_local.record_utterance`): reads 50 ms
     frames. Each frame calls the level callback (the 5th positional
     argument), with a loud level if the room holds her sentence or a
     scripted user line, and a quiet level otherwise. It then keeps the
     frame, so `_Interrupted` behaves as it does in the real recorder
     (`listen_local.py:266-271`). It ends after `hang` of quiet following
     speech, or at `max_seconds`, both on the stepped clock. It returns the
     text it heard as bytes, or `b""`. It sets a `threading.Event` when a
     capture opens and records `self.now` at that moment. From the Nth
     capture on, it blocks on a stop event, so the loop is ended by
     cancelling it, not by a count race. Between frames it may yield the
     thread (`time.sleep(0)`), but no assertion depends on real time
     passing.
   - *Whisper*: `listen_local.transcribe` returns the decoded capture.
   - *Tail sleep*: for tests 5 and 6 only, patch `asyncio.sleep` with a fake.
     The fake records the delay it was asked for, moves `self.now` on by that
     delay, and awaits the real `sleep(0)`. The other tests patch
     `local_engine.ECHO_TAIL_SECONDS` to 0, as `SpokenConsentTests` does.
   - Tests, each driving the real `_listen_loop`, `_inject`, `_typed`,
     `_consent`, `_release` and `_local_confirm`:
     1. `test_a_typed_reply_is_not_heard_as_the_user`: listening, capture
        open, `_inject("what time is it")`. Every mouth record has
        `mic_open == False`, and `brain.asked == ["what time is it"]`.
     2. `test_a_typed_confirm_and_cancel_are_not_heard`: an `EchoBrain`
        hold (`:411`, as in `SpokenConsentTests.held`). A typed "confirm"
        gives "Done." or the release reply, and a typed "cancel" gives
        "Cancelled. reboot was not run.". Same two assertions as test 1.
     3. `test_the_keybind_release_reply_is_not_heard`: a brain hold
        `'reboot'`, capture open, `_local_confirm()` returns "Confirmed:
        reboot" before the mouth plays the reply. Same two assertions.
     4. `test_her_reply_does_not_wake_listening`: muted, `wake_word="oma"`,
        wake capture open, a typed turn whose reply is "Oma, close the
        browser.". `session.active` stays False, and `brain.asked` is only
        the typed line.
     5. `test_a_short_line_gets_the_echo_tail`: the capture hears noise,
        whisper returns "", and she says NOT_CAUGHT. The next capture opens
        no earlier than `_voice_until + ECHO_TAIL_SECONDS`, on the stepped
        clock.
     6. `test_the_user_is_heard_after_the_tail`: a typed turn, then the
        room's next capture holds the user saying "what time is it". It
        opens between 0.35 s and 0.40 s after her last sentence (the tail
        plus at most one frame; the spec measured 0.38 s), and it reaches
        the brain.
     7. `test_a_capture_shut_for_her_voice_is_not_silence`:
        `idle_stop_seconds=60`, `_last_speech -= 600`, capture open, typed
        turn. `session.active` is still True afterwards.
   → verify by `nix develop -c python3 -m pytest -q tests/test_local_engine.py
   -k HerVoiceGatesTheMic` on the unchanged `src/`: **1, 2, 3, 4 and 5
   FAIL**, each for the reason in its name (her reply is in `brain.asked`,
   `mic_open` True at the mouth, `active` True, or capture opened after
   0.00 s). 6 and 7 may pass on `main`. They guard the new code and are
   proven by the mutations in step 7. Every assertion waits on an Event or
   on the stepped clock. None sleeps real time and then checks.
3. **`src/omarchy_voice/local_engine.py` `_record` (`:271-299`) and
   `__init__` (`:179`): shut the capture and wait out the tail.**
   - In `__init__`, next to `_voice_until`: add `self._mic_shut =
     asyncio.Event()` and set it.
   - At the top of `_record`, with `barge_in` off, run the tail wait
     (decision 4). While `_voice_until` is `inf`: if the queue has work or
     `_speaking` is True, `await self._speech.join()`. Otherwise `_say` is
     in its window between setting `inf` and `put`, so `await
     asyncio.sleep(0)` to hand it the loop. Then re-check. Once it is
     finite, compute `delay = self._voice_until + ECHO_TAIL_SECONDS -
     time.monotonic()`. If it is positive, `await asyncio.sleep(delay)` and
     loop again. `ECHO_TAIL_SECONDS` is read as the module global at call
     time, so the tests' patch still applies.
   - `watch`: with `barge_in` off and `_voice_until == math.inf`, raise a
     new `_ShutForHer` (a subclass of `_Interrupted`, or a flag). The
     `except` returns `None` for it and `b""` for the toggle/stop abandon
     (decision 9). The return type becomes `bytes | None`.
   - Clear `_mic_shut` beside `mic_open = True`, and set it in the
     `finally`.
   - Move `_answer`'s tail comment (`:502-505`) here.
   → verify by tests 1, 4 and 5 now passing (2 and 3 may still fail on
   `mic_open`), and by the whole file finishing with no hang, run under
   `timeout 300`.

   > **Spec ambiguity, resolved here.** The spec says the wait loop
   > "re-checks after `join()`" and "must not turn it into a busy spin", but
   > it does not say how. Decision 3 makes `_say` await *between* setting
   > `inf` and `put`. So the window in which `inf` is set and nothing is
   > queued lasts more than one tick. In that window `join()` returns
   > without yielding. A bare re-check loop would spin without ever handing
   > `_say` the event loop, and it would deadlock. The resolution: yield
   > with `asyncio.sleep(0)` only when the queue is empty and nothing is
   > speaking. This is bounded to the ticks `_say` needs to reach `put`. It
   > adds no new state, only `_speech.empty()` and `_speaking`, which both
   > already exist.
4. **`local_engine.py` `_say` (`:244-256`): wait for the mic to shut.** With
   `barge_in` off, between `self._said.append(text)` and `self._speech.put`,
   add `await asyncio.wait_for(self._mic_shut.wait(), 1.0)`. Catch
   `TimeoutError` and log `warn    mic did not shut in 1s`. Then continue:
   decision 2 still protects the transcript. Update the docstring to say the
   gate now runs both ways.
   → verify by tests 1 to 4 passing, including every `mic_open == False`
   assertion.
5. **`local_engine.py` `_turn` (`:302-309`): a shut capture is not
   silence.** After the stop/active check, add `if pcm is None: return`
   before `if not pcm: await self._idle_stop()`. `_wake_turn` (`:363`) needs
   no change.
   → verify by test 7 passing, and by `IdleStopTests` (`:990-1008`) passing
   unchanged.
6. **`local_engine.py` `_answer` (`:501-506`): delete the tail sleep.** Also
   check the `_release` docstring (`:731-732`, "its echo tail"). The tail is
   now kept by the recorder, so reword it if it says otherwise.
   → verify by `grep -n "sleep(ECHO_TAIL" src/omarchy_voice/local_engine.py`
   printing nothing, and by tests 5 and 6 passing.
7. **Mutation checks.** Apply each mutation alone, run the new class, and
   revert (`git checkout -- src/`):
   - M1: drop the `_voice_until == inf` abandon in `watch` → tests 1, 2, 3
     and 4 fail.
   - M2: return `b""` instead of `None` for a capture shut for her voice →
     test 7 fails.
   - M3: remove the tail wait at the top of `_record` → tests 5 and 6 fail.
   - M4: remove the `_mic_shut` await in `_say` → a `mic_open == False`
     assertion in tests 1 to 3 fails.
   - M5: replace the `asyncio.sleep(0)` yield with nothing → the run hangs.
     This must be caught by `timeout 120`, so run it that way.
   - M6: drop the `not barge_in` guard on the tail wait at the top of
     `_record` → the barge-in test in `MicrophoneGateTests` (as #120 left
     it) fails, because the capture never reopens while she is talking. The
     `watch` guard is not covered by this mutation, since the plain `Ears`
     fake never calls the level callback. It is checked by reading the diff.
   → verify by every mutation turning its tests red, and by
   `git diff --stat` showing only the intended files after each revert.
8. **Full suites and the flake.** With
   `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent`, run
   `nix develop -c python3 -m pytest -q`,
   `nix develop -c python3 -m unittest discover -s tests` and
   `nix flake check --no-write-lock-file`
   → verify by all three passing, with the count equal to the step 0
   baseline plus 7. `SpokenConsentTests`, `MicrophoneGateTests`,
   `WatchAnnounceTests`, `ReleaseTurnTests`, `IdleStopTests` and
   `WakeWordTests` must pass with no edits. If the flake check fails only on
   #120's barge-in test under load, report it against #120 and do not
   change that test here.

## Tests

```
export DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent
nix develop -c python3 -m pytest -q tests/test_local_engine.py -k HerVoiceGatesTheMic   # 7 passed (1-5 fail before step 3)
nix develop -c python3 -m pytest -q tests/test_local_engine.py -k SpokenConsent         # unchanged, all pass
nix develop -c python3 -m pytest -q                                                     # baseline + 7, all pass
nix develop -c python3 -m unittest discover -s tests                                    # same count, OK
nix flake check --no-write-lock-file                                                    # passes
```

No test plays audio, opens a microphone or reaches D-Bus. No assertion
depends on how much real time has passed. Everything uses fakes.

## Rollback

The change is one commit on `fix/114-every-voice-gates-the-mic`. Before
merge, drop the branch. After merge, `git revert <sha>` restores the
`_answer` tail sleep and removes the shut/wait. There is no config, state
file or migration. On the host (p620 or razer), the daemon picks it up on the
next `omarchy-voice` rebuild or restart.
