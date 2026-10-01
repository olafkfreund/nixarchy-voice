---
status: draft
issue: 177
author: olafkfreund
---

# Intent: hear with a model sized for the GPU it runs on

Closes #177.

## Problem

Oma mishears the names that matter most on this desktop: her own name, and
the apps she is asked to act on. The persona works around it with a list of
accepted mishearings ("Oma", "Omar", "Ohma", "Alma"), and a misheard app
name costs a full Claude turn to discover.

The model is `base.en` (`nix/whisper-model.nix:60`). It was chosen when
transcription ran on the CPU, where it was the largest model fast enough
for dictation (`nix/whisper-model.nix:57-59`). Since then the Vulkan build
became the default (`nix/package.nix:31`), and the model loads once and
stays loaded (#72). The model was never revisited.

Measured on p620 (RX 7900 XT, RADV, whisper-cpp 1.9.2), with five 3.6 s
desktop commands spoken by the Piper voice and the daemon's vocabulary
prompt (`listen_local.py:144`). Times are per utterance, model already
loaded:

| model | Vulkan | CPU, 4 threads | names heard |
|---|---|---|---|
| `base.en` (today) | 0.075 s | 1.34 s | "Omar", "Vestop"; herdr and Waybar right |
| `large-v3-turbo` q5_0 (574 MB) | 0.175 s | **20.6 s** | Oma, Vesktop, Waybar right; "Herder" |
| Parakeet TDT 0.6B v3 q8 | 0.082 s | — | "Vestop", "Herder", "hyperlint", "Waiber" |

The Claude turn behind these takes 1.3-19 s (`TIMING` lines in
`session.log`, 2026-09-27). On the GPU, speech recognition is not where
the time goes, so a bigger model costs nothing the user would notice.

## Proposed outcome

- On a machine where whisper runs on the GPU, Oma hears names right more
  often: her own, the apps in the vocabulary prompt, and the desktop's own
  words. The persona's list of accepted mishearings is no longer needed as
  often.
- A machine without a usable GPU is no slower than it is today.
- `omarchy-voice doctor` says which model is in use and whether whisper is
  on the GPU, so a 20 s transcription is never a mystery.
- README and the project page show the new disk size.

## Affected users and systems

- p620, where it was measured: RX 7900 XT, Vulkan.
- razer: not checked. Its GPU and Vulkan state decide which side of the
  constraint below it lands on.
- Anyone on nixarchy who enables Voice (`programs.nixarchy.voice.enable`),
  and anyone overriding `whisperImpl = pkgs.whisper-cpp` for a VM, a
  headless box or an old card (`nix/package.nix:28-30`).
- `nix/whisper-model.nix`, `nix/package.nix`, `listen_local.py` (doctor
  checks), README (`:41`, `:320-330`), `docs/index.html:164`.
- Unchanged: `[ears] whisper_model` and `OMARCHY_VOICE_WHISPER_MODEL`
  still override whatever is chosen.

## Constraints

- **The model is chosen at build time; the GPU is found at run time.** A
  Vulkan build on a machine whose only Vulkan device is llvmpipe (p620
  lists llvmpipe as a second device), or with no device at all, runs
  `large-v3-turbo` on the CPU at about 20 s an utterance. That must never
  be a silent outcome.
- The CPU build (`whisperImpl = pkgs.whisper-cpp`) must keep a model that
  is fast on the CPU.
- No first-use download: the model stays a Nix store path (the reason
  `whisper-model.nix` exists, `:4-9`).
- Recognition stays English-only (`-l en`) in this task. The turbo model
  is multilingual, but the persona and wake word are English.
- The vocabulary prompt must keep working. That rules out Parakeet as
  the default (no prompt option in `parakeet-cli`).
- The persona's mishearing list is not removed in this task; real use
  first shows whether it still earns its place.
- Disk: about +430 MB on top of the current 6.7 GiB.

## Open questions

1. Validation before the default changes: is the synthetic test enough,
   or should a set of real microphone recordings from p620 be compared
   first? (Recommended: about ten real utterances, compared once.)
2. A Vulkan build with no usable GPU: fall back to `base.en` at run time
   (ship both models, about +142 MB), or keep one model and have `doctor`
   and the start log warn loudly?
3. `q5_0` (574 MB) or `q8_0` (~870 MB)? Only q5_0 was measured.
4. Keep Parakeet on record as a possible future engine (if whisper.cpp
   ships a Parakeet server or vocabulary support), or close that door here?
