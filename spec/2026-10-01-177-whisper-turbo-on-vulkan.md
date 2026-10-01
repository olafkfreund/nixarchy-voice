---
status: draft
issue: 177
intent: intent/2026-10-01-177-whisper-turbo-on-vulkan.md
---

# Spec: hear with a model sized for the GPU it runs on

## Decisions on the intent's open questions

The intent was approved with "go with your recommendations". Where the
intent gave none, the decision is made here with the reason.

1. **Validation: ten real recordings, compared once, before the default
   ships.** This was the intent's recommendation. See Verification V3.
2. **No usable GPU: fall back to `base.en` at run time, shipping both
   models (+142 MB).** The intent gave no recommendation; decided here.
   A warning alone leaves the user with 20.6 s per utterance until they
   act, and the voice is unusable in the meantime. The case is real, not
   hypothetical: nixarchy has a microvm Hyprland template
   (`nixarchy/modules/microvm/templates/hyprland.nix`) that already
   carries voice tooling, and a VM has no GPU. One fallback model buys a
   working voice everywhere for 142 MB.
3. **`q5_0`.** The intent gave no recommendation; decided here. It is
   the only variant measured. `q8_0` costs about 300 MB more for a
   difference this task has no way to hear on five sentences.
4. **Parakeet stays on record, not built.** The intent gave no
   recommendation; decided here. It is listed under Alternatives rejected
   with the condition that would reopen it. Recording it costs nothing.

## Design

### Packaging

- `nix/whisper-model.nix`: add a `"large-v3-turbo-q5_0"` entry from the
  same `ggerganov/whisper.cpp` repo (`ggml-large-v3-turbo-q5_0.bin`,
  574,041,195 bytes, sha256 base32
  `1qm7zxamlvac564c3270wqqqks5wc7532q3fqi01zbfmkiq22hir`, hashed from the
  file downloaded on 2026-10-01). `default` becomes this entry. The
  comment at `:57-59` is rewritten to say why: the GPU now does the work,
  and the CPU path has its own model (below). `base.en` stays as an entry.
- `nix/package.nix`: a second argument, `whisperCpuModel ? (callPackage
  ./whisper-model.nix { })."base.en"`, exported in the wrapper as
  `OMARCHY_VOICE_WHISPER_CPU_MODEL` next to the existing
  `OMARCHY_VOICE_WHISPER_MODEL` (`:117-118`). The comments at `:36-40`
  are updated to describe both.

### Choosing at run time

whisper.cpp says on stderr whether it found a GPU. Observed with
whisper-cpp 1.9.2 on p620:

- GPU: `whisper_backend_init_gpu: device 0: Vulkan0 (type: 1)`.
- No Vulkan device or driver, and the CPU build:
  `whisper_backend_init_gpu: no GPU found`.

`listen_local.Server.start` (`listen_local.py:184-211`) changes like this:

- The server's stderr goes to a file,
  `RUNTIME_DIR/whisper-server.log`, truncated on each start. Today it goes
  to `DEVNULL`. A file, not a pipe: a pipe nobody drains blocks the server
  once the buffer fills.
- Once the port answers, the file is read. If it contains `no GPU found`
  and the model in use is the packaged default, the server is stopped and
  started again on `OMARCHY_VOICE_WHISPER_CPU_MODEL`. The session log gets
  one line saying so, and naming both models.
- The rule only applies to the packaged default. A model the user set
  (`[ears] whisper_model`, or their own `OMARCHY_VOICE_WHISPER_MODEL`)
  is never swapped. They chose it.
- The fallback is remembered for the rest of the process. If the server
  later fails, `transcribe`'s `whisper-cli` path (`listen_local.py:359-381`)
  uses the same model as the server, so it cannot drop back to a 20 s
  model.
- If the server never starts (no `whisper-server`), `whisper-cli` runs
  the default model, as today. The server ships in the same package as
  `whisper-cli`, so this only happens on a broken install.

`model_path` (`:250-253`) keeps its precedence, with one addition: an
explicit setting, then the environment, then the CPU model once the
fallback has fired.

### Telling the user

- The `start` line in `session.log` (`local_engine.py:1123-1127`) already
  says `stt=whisper.cpp`. It becomes `stt=whisper.cpp <model> on
  <Vulkan0|CPU>`, read from the server log, so the log answers "which
  model, where" for every session.
- `doctor`'s **ears** section (`cli.py`, around `:479-505`) adds one line:
  the model in use and whether it is on the GPU. The line is read from
  `RUNTIME_DIR/whisper-server.log` if the daemon is running, and says
  "unknown until the daemon starts" otherwise. If the fallback fired, the
  line says so and why.

### Documentation

- README `:41` and `docs/index.html:164`: new disk size, measured with
  `nix path-info -Sh` on the built package rather than estimated.
- README `:320-330` (the model section): turbo on the GPU, `base.en` on
  the CPU, the automatic fallback, and the `whisperModel` /
  `whisperCpuModel` overrides.

## Alternatives rejected

- **Keep `base.en`.** It mishears the names the vocabulary prompt exists
  to fix, on hardware that could run a model that hears them.
- **Warn instead of falling back.** See decision 2: a warning still
  leaves the voice unusable until the user acts.
- **Pick the model at build time from `whisperImpl`.** It does not help
  a Vulkan build on a machine with no GPU, which is the case that matters,
  and the run-time check covers the CPU build too.
- **Parakeet TDT 0.6B v3 through `parakeet-cli`.** It is as fast as
  `base.en` on Vulkan and is in the same build. But it has no prompt
  option, so it cannot be told the desktop's names, and it heard them
  worst of the three. whisper.cpp also ships no Parakeet server, so the
  daemon would pay a 1.3 s load per utterance. **Reopen if** whisper.cpp
  gains a Parakeet server or vocabulary biasing.
- **achetronic/parakeet.** ONNX Runtime with CPU or CUDA only, so it
  cannot use an AMD GPU.
- **FunASR.** PyTorch and CUDA, with Chinese-first models.
- **`q8_0`.** See decision 3.

## Risks

- **VRAM.** The turbo model holds about 0.6 GB resident on the GPU,
  alongside games and Ollama on p620. Check: `whisper_model_load: Vulkan0
  total size` in the server log, and the free VRAM on p620 under normal
  load.
- **First load.** The first start after boot compiles shaders (about
  1.2 s with `base.en`, per `package.nix:24`) and reads 574 MB. This is
  hidden behind the brain's 6.5 s warm-up (`local_engine.py:1131-1134`),
  but should be measured to confirm.
- **The log string changes in a future whisper.cpp.** If `no GPU found`
  stops matching, the fallback never fires and the user is back at 20 s.
  Mitigation: a test pins the string against the packaged binary, run by
  `nix develop` on a live session as the suite already is
  (`package.nix:122-125`). `doctor` shows the device either way.
- **razer.** Its config sets `GGML_VK_VISIBLE_DEVICES=1`
  (`hosts/razer/nixos/nixarchy.nix:170`), so whisper is meant to run on a
  GPU there. Not measured. Checked during verification, not assumed.
- **Disk.** About +574 MB: the turbo model, with `base.en` kept as the
  fallback.

## Verification

- **V1, build.** `nix build .#default`, then confirm the wrapper exports
  both `OMARCHY_VOICE_WHISPER_MODEL` (turbo) and
  `OMARCHY_VOICE_WHISPER_CPU_MODEL` (base.en). `nix flake check` passes.
- **V2, tests.** Unit tests with a fake server log:
  - `no GPU found` → restart on the CPU model;
  - a Vulkan line → no restart;
  - a user-set model with `no GPU found` → no swap;
  - after the fallback, `whisper-cli` uses the CPU model.

  One live test checks that the packaged `whisper-server`, started with
  `VK_ICD_FILENAMES=/nonexistent`, prints `no GPU found`. The full suite
  passes.
- **V3, real recordings (decision 1).** The owner records ten real
  commands on p620's microphone (`pw-record --rate 16000 --channels 1`),
  including Oma's name and app names from the vocabulary prompt.
  `base.en` and turbo transcribe each one with the daemon's prompt. The
  default ships only if turbo gets at least as many of the names right as
  `base.en` and no sentence gets worse. Results go in the plan.
- **V4, live on p620.** The daemon starts, and `session.log` shows `turbo
  … on Vulkan0`. A spoken command is heard correctly, and `TIMING` shows
  no regression.
- **V5, forced fallback on p620.** With `Environment=VK_ICD_FILENAMES=/nonexistent`
  in a drop-in, the daemon logs the fallback, transcribes with `base.en`,
  and `doctor` says so. Remove the drop-in afterwards.
- **V6, razer.** The `start` line on razer names the device whisper used.
