---
status: approved
issue: 178
intent: intent/2026-10-01-178-eleven-v4-turbo-default.md
---

# Spec: speak with eleven_v4_turbo by default

## Decisions on the intent's open questions

Approved with "go with your recommendations":

1. **Mastering gain: re-measured, and it stays at +6 dB.** It was
   re-measured on #135's five sentences (plan O2) with v4 turbo, voice
   `7cOBG34AiHrAzs842Rdi`, stability 0.5 and similarity 0.75. Raw
   integrated loudness: −21.4, −22.7, −21.5, −23.5, −20.5 LUFS, mean
   −21.92. **G = −16 − (−21.92) = 6.08, rounded to 6.0 dB**, the same as
   turbo v2.5's 5.98 (#135). The intent's "2.7 dB louder" came from one
   sentence and does not hold over five. Mastered at +6 dB: −16.7, −16.8,
   −16.0, −17.7, −15.9. Four are inside #135's ±1.5 window. Clip 4 is
   0.2 dB under it, the longest and quietest sentence. That does not
   justify a different fixed gain. **`elevenlabs_master` is not
   changed.**
2. **`doctor` flags only the exact old default, `eleven_turbo_v2_5`.**
   Any other pinned model was chosen.
3. **A refused model falls back to Piper, as today.** No second cloud
   attempt.
4. **Released as 2.4.0.** The release notes tell users with a copied
   `model = "eleven_turbo_v2_5"` line to delete it.

## Design

### The default

- `src/omarchy_voice/config.py:501`:
  `elevenlabs_model: str = "eleven_v4_turbo"`.
- `share/config.example.toml:201-205`: the `model` line is **commented
  out**, so copying the example no longer pins anything. The comment says
  the default is `eleven_v4_turbo`, why (same first-byte latency and
  price as turbo v2.5, better delivery), and that setting `model` pins
  you to it. `stability` and `similarity` stay uncommented, as they are
  not defaults that move. The `master` comment (`:207-210`) gains "re-
  measured for eleven_v4_turbo on #178: still +6 dB".
- `tests/test_elevenlabs.py:531`: expects `eleven_v4_turbo`.

### `doctor` checks the cloud voice

`elevenlabs.check_ready` (`elevenlabs.py:311-330`) has no callers today,
so `doctor` never checks the cloud voice. It is wired in, and extended to
answer "what is needed from the user":

- In `doctor`'s **engine** section, after the voice chain line
  (`cli.py`, around `:461-467`):
  - Cloud voice **off** (`enabled = false`): one line saying replies use
    Piper, then the four things the cloud voice needs (account, key,
    `voice_id`, `enabled = true`), in the order a user does them.
  - Cloud voice **on**: each problem `check_ready` returns, or a tick.
- `check_ready` gains two checks. Both use free `GET` calls, so no
  characters are spent:
  - **Model available.** `GET /v1/models`. The configured model must be
    listed with `can_do_text_to_speech`. Otherwise: "model X is not
    offered to this account; replies will fall back to Piper". This
    covers a typo, a retired model and a plan restriction.
  - **Voice served.** `voices()` already fetches `/v1/voices`; it starts
    keeping each voice's `category`. If the configured voice is
    `professional` and the model reports `serves_pro_voices: false`, say
    so. Today no model in this account's list serves professional clones
    on this endpoint. This is the one refusal a user can hit by picking a
    voice.
- **The pin notice (decision 2).** If `config.elevenlabs_model ==
  "eleven_turbo_v2_5"`, `doctor` prints one line: the config pins the
  previous default; delete `model` under `[elevenlabs]` to use
  `eleven_v4_turbo`. Now that the default has changed, that value can only
  come from the user's own config, so there is no need to tell a set value
  from a defaulted one.

### What the user needs, written down once

README's ElevenLabs section gets the four steps as a numbered list,
matching what `doctor` prints: free tier is enough, the key through
`secret-tool` or `elevenLabsKeyFile`, `omarchy-voice voices` for the id,
`enabled = true`. The model is not a step, because the default is the
recommendation.

### Release (decision 4)

Version 2.4.0, bumped in the same five places as 2.3.1 (`124ef41`):
`nix/package.nix`, both plugin `manifest.json`, `pyproject.toml`,
`__init__.py`. The GitHub release notes carry the delete-the-pin line.

## Alternatives rejected

- **Rewrite a pinned `eleven_turbo_v2_5` to v4 at load time.** It cannot
  tell a copied pin from a chosen one, and it would override a user's
  setting without asking. Advice from `doctor` is the most the daemon may
  do.
- **`eleven_v3_conversational`.** Faster first byte (0.12 s) and the same
  price, but the owner preferred v4 turbo by ear.
- **`eleven_flash_v2_5`.** Fastest of the old generation, but no better
  sounding than today.
- **Retry on turbo v2.5 before Piper.** See decision 3: a second fallback
  path to keep working, for a failure `doctor` now explains in advance.
- **A new fixed gain.** See decision 1: the measurement says +6 dB.

## Risks

- **v4 turbo is newer.** If ElevenLabs changes or retires it, every
  unpinned user falls back to Piper. The fallback logs why, and `doctor`'s
  model check names the cause. Recovery is one config line.
- **Character limit.** v4 turbo allows 10,000 characters per request
  against turbo v2.5's 40,000. Replies are one short sentence, and the
  daemon synthesises sentence by sentence, so the limit is far away.
- **The `/v1/models` call in `doctor`** adds one network round trip, only
  when the cloud voice is on. `doctor` already calls `/v1/voices`.
- **p510.** Same voice and settings as p620, so it gets v4 on update. Not
  measured there.

## Verification

- **V1, tests.**
  - The default model is `eleven_v4_turbo`.
  - The example config parses with no `model` key.
  - `check_ready` with fake `/v1/models` and `/v1/voices` responses:
    model listed, model missing, professional voice on a model that does
    not serve them.
  - The pin notice prints for `eleven_turbo_v2_5` and not for any other
    model.
  - `doctor` with the cloud voice off prints the four steps.

  The full suite passes.
- **V2, build.** `nix build .#default` and `nix flake check`.
- **V3, live on p620.** After switching, `session.log` shows
  `voice=ElevenLabs eleven_v4_turbo`, and a spoken reply plays. `TIMING`
  shows `synth` first-byte in line with today (0.2-0.5 s request). The
  owner listens to one turn.
- **V4, doctor on p620.** It shows the cloud voice ticks, and no pin
  notice, since p620's config sets no model. With `model =
  "eleven_turbo_v2_5"` temporarily set, it shows the notice.
