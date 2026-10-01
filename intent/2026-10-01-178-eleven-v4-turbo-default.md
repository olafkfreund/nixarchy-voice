---
status: approved
issue: 178
author: olafkfreund
---

# Intent: speak with eleven_v4_turbo by default

Closes #178.

## Problem

The cloud voice uses `eleven_turbo_v2_5` (`config.py:501`). ElevenLabs now
offers newer models at the same price, and the owner prefers how
`eleven_v4_turbo` sounds after listening to all four side by side.

Measured on p620 on 2026-10-01: the configured voice, the same text and
settings, and the streaming endpoint the daemon uses (#135):

| model | first byte | cost per character |
|---|---|---|
| `eleven_turbo_v2_5` (today) | 0.21 s | 0.5x |
| `eleven_flash_v2_5` | 0.14 s | 0.5x |
| `eleven_v3_conversational` | 0.12 s | 0.5x |
| `eleven_v4_turbo` (chosen) | 0.16 s | 0.5x |

Because playback starts on the first byte, the change costs no waiting and
no extra quota.

Three things stand between "the default changed" and "users hear it":

1. **The example config pins the old model.** `share/config.example.toml:205`
   ships `model = "eleven_turbo_v2_5"` uncommented. Anyone who copied the
   example has pinned the old model without choosing it, and a new default
   never reaches them.
2. **The mastering gain was measured for the old model.** `master =
   "volume=6dB,..."` came from turbo v2.5 clips (#135, plan O2: mean −21.98
   LUFS). v4 turbo's raw output measured −20.6 LUFS, 2.7 dB louder than the
   old model's −23.3 on the same sentence. Through today's chain it still
   lands at −16.2 LUFS (target −16), but the limiter does more of the work.
   That is one sentence, not the five-sentence set #135 used.
3. **Nothing tells a user they are pinned.** `doctor` prints the model in
   the chain line (`local_engine.py:1202`), but it reads the same whether it
   is the default or a stale pin.

## What a user needs today to hear ElevenLabs at all

This task does not change these. They are listed because "everyone gets
v4" only means everyone who already has the cloud voice on:

- An ElevenLabs account. The free tier works: v4 turbo needs no alpha
  access and allows up to 10,000 characters per request on free accounts.
- An API key, in the keyring
  (`secret-tool store --label omarchy-voice service omarchy-voice-elevenlabs`)
  or through the Home Manager `elevenLabsKeyFile` option.
- A voice id from their own account (`omarchy-voice voices`), set as
  `[elevenlabs] voice_id`. There is deliberately no default
  (`config.example.toml:192-194`).
- `[elevenlabs] enabled = true`.

Without all four, replies are spoken by Piper, as today.

## Proposed outcome

- A user with the cloud voice on and no `model` set hears `eleven_v4_turbo`
  after updating. No action needed from them.
- A user pinned to an older model by the example config is told so by
  `doctor`, once, with the one line to delete. A deliberate pin is
  respected: the daemon never overrides a model the user set.
- Loudness stays at the −16 LUFS target with the new model.
- If the model is refused for a given account or voice, the reply is still
  spoken (Piper fallback, logged), and `doctor` says why.

## Affected users and systems

- p620 and p510: cloud voice on, no `model` set. Both get v4 on update.
- razer: the cloud voice was not checked in its config.
- Anyone with the cloud voice on, through nixarchy's Voice entry or this
  flake's Home Manager module.
- `config.py:501`, `share/config.example.toml:201-205` and `:209-215`
  (`master`), `cli.py` (`doctor`), `tests/test_elevenlabs.py:531`, README.

## Constraints

- Never override a model the user set on purpose. The daemon cannot tell a
  copied pin from a chosen one, so it may only advise, never rewrite.
- The Piper fallback stays: any ElevenLabs failure is still spoken, not
  silent.
- No new quota cost: v4 turbo has the same 0.5x multiplier.
- `style` stays out of the request (`elevenlabs.py:190`). v4 turbo reports
  `can_use_style: false` anyway.
- Users without ElevenLabs are unaffected.

## Open questions

1. Mastering gain: re-measure on #135's five sentences and set a new fixed
   gain for v4 (expected around +3.5 dB)? Or keep +6 dB, since the limiter
   already lands at −16.2? (Recommended: re-measure. It is the method #135
   set, and a gain tuned for another model is a guess.)
2. Pinned users: should `doctor` flag only the exact old default
   (`eleven_turbo_v2_5`), or any model that is not the current default?
   (Recommended: only the old default. Any other pin was chosen.)
3. If v4 turbo is refused (a voice or tier it does not serve), fall back to
   Piper as today, or retry once on `eleven_turbo_v2_5` first?
   (Recommended: Piper, as today. One fallback path, already tested.)
4. Release: a minor version (2.4.0) with a changelog line telling users to
   delete a copied `model =` line?
