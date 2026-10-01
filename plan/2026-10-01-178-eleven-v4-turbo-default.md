---
status: approved
issue: 178
spec: spec/2026-10-01-178-eleven-v4-turbo-default.md
---

# Plan: speak with eleven_v4_turbo by default

## What was decided (carried over from the approved spec)

- The default ElevenLabs model becomes `eleven_v4_turbo`. On p620 its
  first byte took 0.16 s, against 0.21 s for `eleven_turbo_v2_5`, at the
  same 0.5x character cost. The owner chose it by ear.
- **Mastering stays at `volume=6dB,alimiter=limit=0.84:level=0`.** It was
  re-measured on #135's five sentences with v4 turbo, voice
  `7cOBG34AiHrAzs842Rdi`, stability 0.5, similarity 0.75. Raw: −21.4,
  −22.7, −21.5, −23.5, −20.5 LUFS, mean −21.92, so G = 6.0 dB. Mastered:
  −16.7, −16.8, −16.0, −17.7, −15.9.
- The example config stops pinning a model.
- `doctor` checks the cloud voice. Today `elevenlabs.check_ready` has no
  callers. It says:
  - with the cloud voice off: the four things it needs (account, key,
    voice id, `enabled = true`);
  - with it on: each problem, including a model this account is not
    offered, and a professional voice the model does not serve;
  - when the config pins the old default `eleven_turbo_v2_5`: one line to
    delete it. No other model is flagged.
- A refused model falls back to Piper, as today. Nothing rewrites a user's
  config.
- Released as 2.4.0. The release notes say to delete a copied
  `model = "eleven_turbo_v2_5"`.
- **Merge order:** #177 first, then this. The 2.4.0 bump is in this plan
  (step 6), so both changes ship in one release.

### Where this plan refines the spec

- **R1.** `_get` (`elevenlabs.py:99-102`) is annotated `-> dict`, but
  `GET /v1/models` returns a JSON **list**. The new `models()` helper
  accepts a list and does not trust the annotation. The annotation
  becomes `-> dict | list`.
- **R2.** The configured voice may be a library voice that is not in the
  account's `/v1/voices` list. When it is not found, its category is
  unknown and no professional-voice problem is reported. Absent is not
  the same as wrong.
- **R3.** The doctor logic goes in a helper, `cli._cloud_voice_lines(config)
  -> list[str]`, so it can be tested without running all of `doctor`.
  This follows `_hands_tools` (`tests/test_doctor_hands.py`).

- **R4 (found in step 3).** The plan's model-problem wording pointed at
  `omarchy-voice voices` for models. That command lists voices, not models,
  and it turns out not to exist at all (see R5). The message now says to
  delete `model` or set one the account offers.
- **R5 (found in step 3, open).** There is no `omarchy-voice voices`
  subcommand, and there never has been. Yet `config.py:490`,
  `share/config.example.toml:192` and `elevenlabs.py:330` (and this plan's
  doctor text) send users to it for their voice id. Resolution: owner
  decision pending.
- **Test order (step 3).** Two existing tests in `tests/test_elevenlabs.py`
  were fixed in step 3 instead of step 5, so the suite never reaches the
  network between steps: `test_a_fully_configured_setup_reports_nothing`
  now mocks `models`, and the expected dict gains `category`. The
  `model_id` assertion at `:531` moved with them, since it failed as soon
  as step 1 changed the default.

## Steps

1. **`src/omarchy_voice/config.py:497-501`: the default**
   - Set `elevenlabs_model: str = "eleven_v4_turbo"`.
   - Rewrite the comment above it: v4 turbo, chosen in #178. Same
     first-byte latency and price as turbo v2.5, and better delivery.
     Still not `style > 0` (v4 turbo reports `can_use_style: false`
     anyway).
   - In the master comment (`:512-513`), add a line: re-measured
     2026-10-01 for `eleven_v4_turbo` on the same five sentences (raw
     mean −21.92 LUFS), and G is still 6.0 dB (#178).

   → verify: `python3 -c 'from omarchy_voice.config import Config; print(Config().elevenlabs_model)'`
   (with `PYTHONPATH=src`) prints `eleven_v4_turbo`.
   Traps: none.

2. **`share/config.example.toml:201-205`: stop pinning**
   - Replace the comment and `model = "eleven_turbo_v2_5"` with:
     ```toml
     # The model defaults to eleven_v4_turbo: as quick to start speaking as
     # turbo v2.5, same price, better delivery (#178). Setting it pins you
     # to that model when the default moves on. style stays 0 in code.
     # model = "eleven_v4_turbo"
     ```
   - Leave `stability`, `similarity` and `master` as they are.
   - In the master comment (`:213`), change "measured on #135 for the
     default voice" to "measured on #135, re-measured for v4 turbo on
     #178".

   → verify: step 5's example-parse test.
   Traps: the example is copied by users, so nothing in it may be an
   uncommented value that pins a moving default.

3. **`src/omarchy_voice/elevenlabs.py`: what `check_ready` checks**
   - `_get` annotation: `-> dict | list` (R1).
   - `voices()` (`:120-129`): each listed dict gains
     `"category": voice.get("category", "")`.
   - New function, after `voices()`:
     ```python
     def models(config: Config) -> list[dict]:
         """The models this account is offered, from GET /v1/models (#178)."""
     ```
     It raises `Unavailable` on errors, exactly like `voices()`
     (`:114-119`). It returns the list, or `[]` if the reply is not a
     list.
   - `check_ready` (`:311-330`), after `voices(config)` succeeds:
     - Keep the voices result.
     - Call `models(config)`, catching `Unavailable` and appending its
       message.
     - Find the configured model. If it is missing, or lacks
       `can_do_text_to_speech`, append: `f"model {model} is not offered to
       this account: delete model under [elevenlabs] or set one it offers;
       replies will
       fall back to Piper"`. The wording names the cause and the outcome.
     - Find the configured voice in the voices list. If it is present
       with `category == "professional"`, and the model has
       `serves_pro_voices` false, append: `f"voice {voice_id} is a
       professional clone, which {model} does not serve on this account;
       replies will fall back to Piper"`. If the voice is absent, report
       nothing (R2).

   → verify: step 5's `CheckReadyTests`.
   Traps:
   - **The existing test `test_a_fully_configured_setup_reports_nothing`
     (`tests/test_elevenlabs.py:144-148`) mocks only `voices`.** Once
     `check_ready` also calls `models`, that test would make a real HTTP
     call to ElevenLabs. Mock `models` in it too, returning a listed
     `eleven_v4_turbo`.
   - `test_a_realistic_payload_becomes_name_id_and_labels` (`:177-185`)
     compares a whole dict, so add `"category": "premade"` to its
     expected value.
   - Use `voice.get`, never `voice[...]`: the fixtures already include a
     voice with no `category` (`:172-175`).

4. **`src/omarchy_voice/cli.py`: `doctor` shows the cloud voice (R3)**
   - Add `_cloud_voice_lines(config) -> list[str]` next to `_hands_tools`.
   - **Cloud voice off** (`not config.elevenlabs_enabled`): return
     ```
     → cloud voice off: replies are spoken by Piper. For ElevenLabs:
         1. an account at elevenlabs.io (the free tier works)
         2. the API key: secret-tool store --label omarchy-voice service <slot>
            (or elevenLabsKeyFile in the Home Manager module)
         3. a voice: omarchy-voice voices, then voice_id under [elevenlabs]
         4. enabled = true under [elevenlabs]
     ```
     `<slot>` is `config.elevenlabs_key_slot`.
   - **Cloud voice on:** the ticked header `f"{_tick(ok)} cloud voice:
     ElevenLabs {config.elevenlabs_model}"`, then each `check_ready`
     problem, wrapped at 70 characters in the same style as
     `engine_problems` (`:470-475`).
   - **Either case:** if `config.elevenlabs_model == "eleven_turbo_v2_5"`,
     append: `→ your config pins eleven_turbo_v2_5, the previous default.
     Delete model under [elevenlabs] to use eleven_v4_turbo.`
   - In `cmd_doctor`, print these lines right after the `engine_problems`
     block (after `:477`), inside the **engine** section.

   → verify: step 5's `CloudVoiceDoctorTests`, then
   `omarchy-voice doctor` on p620.
   Traps:
   - `check_ready` makes network calls. The helper may only call it when
     the voice is enabled, so a user with ElevenLabs off never reaches
     the network.
   - `_tick` is defined in `cli.py`; reuse it.

5. **Tests**
   - `tests/test_elevenlabs.py`:
     - `:531`: expect `"eleven_v4_turbo"`.
     - In `CheckReadyTests`, fix the two existing tests (step 3, traps)
       and add:
       - a. The model is listed and the voice is premade → `[]`.
       - b. The model is absent from `models` → one problem naming the
         model and "Piper".
       - c. The voice is `professional` and the model has
         `serves_pro_voices: False` → one problem.
       - d. The voice is not in the list → no voice problem (R2).
       - e. `models` raises `Unavailable` → reported, not thrown.
     - `VoiceListingTests`: add a test that `models` parses a list
       payload, using `_urlopen` with a list.
   - `tests/test_config.py`:
     - `Config().elevenlabs_model == "eleven_v4_turbo"`.
     - The example config parses through the same loader the config
       tests already use, and its `[elevenlabs]` table has no `model` key.
   - New `tests/test_doctor_voice.py`, modelled on `test_doctor_hands.py`.
     Import `_isolated` first.
     - Off → the lines contain the four steps and the key slot.
     - On, with `check_ready` mocked to `[]` → a ticked header naming the
       model.
     - On, with `check_ready` mocked to return a problem → the problem
       appears.
     - `eleven_turbo_v2_5` → the pin line; `eleven_flash_v2_5` → no pin
       line.
     - Off → `check_ready` is never called.

   → verify: `nix develop -c pytest tests -q` passes in full, and no test
   reaches the network.
   Traps: `_key_for_slot` is cached. Call `cache_clear()` in `setUp`, as
   the existing classes do (`:124-125`).

6. **Release 2.4.0 and documentation**
   - README:
     - Add a section, `### Her cloud voice`, between the end of "Her
       local voice" (`:393`) and "Speech without OpenAI" (`:395`). It
       holds the four steps, numbered, with the same wording as `doctor`.
       Say the default model is `eleven_v4_turbo` and that setting `model`
       pins it. Add one line on loudness: mastering is +6 dB, re-measured
       for v4 turbo.
     - Line 384, "the primary one is ElevenLabs, next.", now has a section
       to point at. Link it.
     - Line 458 (Requirements): link the new section instead of "Speech
       without OpenAI", and say the free tier works.
   - Version `2.3.1` → `2.4.0` in the same five files as `124ef41`:
     `nix/package.nix`, `plugin/olafkfreund.voice-indicator/manifest.json`,
     `plugin/olafkfreund.voice-orb/manifest.json`, `pyproject.toml` and
     `src/omarchy_voice/__init__.py`. Commit this as `chore(release):
     2.4.0` on its own branch after #177 and #178 are both merged, as was
     done for 2.3.1.
   - Release notes, drafted in the PR for the owner to publish:
     - The new default voice model.
     - "if your config.toml has `model = "eleven_turbo_v2_5"` under
       [elevenlabs], delete it; `omarchy-voice doctor` tells you".
     - The turbo whisper model from #177.

   → verify:
   `grep -rn "2\.3\.1" nix/package.nix plugin/*/manifest.json pyproject.toml src/omarchy_voice/__init__.py`
   finds nothing after the release commit.
   Traps: the release commit is separate from this PR (it follows the
   2.3.1 pattern: `chore/release-2.4.0`). Do not bump the version inside
   the feature PR.

## Tests

- `nix develop -c pytest tests -q`: everything passes.
- `nix flake check --print-build-logs` passes.
- On p620 (spec V3/V4), using a user-space drop-in pointing at the built
  package (see memory "live-testing omarchy-voice on p620"), not
  nixos-rebuild:
  - After a restart, `session.log`'s `start` line shows
    `voice=ElevenLabs eleven_v4_turbo, piper if it fails`.
  - One spoken turn plays, and `TIMING` shows `synth` request in the
    usual 0.2-0.5 s range.
  - The owner listens to it.
- `omarchy-voice doctor` on p620 shows the cloud voice ticked, with no
  pin line.
- The pin line, without touching the real config: p620's
  `~/.config/omarchy-voice/config.toml` is a read-only Home Manager
  symlink. Copy it into the scratchpad, add `model = "eleven_turbo_v2_5"`
  under `[elevenlabs]`, and run `omarchy-voice --config <copy> doctor`.
  The global `--config` flag is at `cli.py:848`. The pin line appears.

## Rollback

- Revert the merge commit. The default goes back to `eleven_turbo_v2_5`
  and `doctor` loses the cloud-voice lines. Nothing is persisted.
- Per user, with no revert: set `model = "eleven_turbo_v2_5"` under
  `[elevenlabs]`. With this change in place, `doctor` will then suggest
  deleting it, which is expected for a deliberate pin.
