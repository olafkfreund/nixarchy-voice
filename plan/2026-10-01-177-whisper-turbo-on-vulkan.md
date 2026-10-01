---
status: approved
issue: 177
spec: spec/2026-10-01-177-whisper-turbo-on-vulkan.md
---

# Plan: hear with a model sized for the GPU it runs on

## What was decided (carried over from the approved spec)

- The default whisper model becomes `large-v3-turbo-q5_0`
  (`ggml-large-v3-turbo-q5_0.bin` from `ggerganov/whisper.cpp`,
  574,041,195 bytes, sha256 base32
  `1qm7zxamlvac564c3270wqqqks5wc7532q3fqi01zbfmkiq22hir`).
- `base.en` ships too, as the CPU model. When whisper reports
  `whisper_backend_init_gpu: no GPU found` on stderr, the daemon swaps to
  it. A model set in `[ears] whisper_model` is never swapped.
- Why: on p620 (RX 7900 XT, Vulkan), turbo transcribes a 3.6 s utterance
  in 0.175 s and hears the desktop's names best. On a CPU it takes 20.6 s,
  so falling back is needed for VMs and machines without a GPU.
- `q5_0`, not `q8_0`. Parakeet, achetronic/parakeet and FunASR are
  rejected. Parakeet is reopened only if whisper.cpp gains a Parakeet
  server or vocabulary biasing.
- The session log and `doctor` say which model is in use and on which
  device.
- Before the default ships, ten real recordings from p620's microphone
  must show turbo hears at least as many names right as `base.en`, with no
  sentence worse (step 9).
- No version bump in this task. The 2.4.0 release happens in #178's plan,
  which merges after this one.

Observed whisper-cpp 1.9.2 stderr, which the code relies on:

```
GPU:     whisper_backend_init_gpu: device 0: Vulkan0 (type: 1)
no GPU:  whisper_backend_init_gpu: device 0: CPU (type: 0)
         whisper_backend_init_gpu: no GPU found
```

Both lines are printed before the server opens its port.

### Where this plan refines the spec

Each of these was found while tracing the code for this plan:

- **R1. `omarchy-voice ask` never starts the server.** It calls
  `listen_local.transcribe(pcm, config)` (`cli.py:75`), which runs
  `whisper-cli`, so the spec's fallback would not reach it. On a machine
  without a GPU, `ask` would take 20 s. `ask` now starts a `Server` for its
  one transcription and stops it afterwards (step 5). The server takes
  about as long to load as `whisper-cli` does, so this costs nothing, and
  both paths now share one fallback.
- **R2. The `start` line is logged before whisper has started**
  (`local_engine.py:1124`, with the server awaited at `:1150`). The model
  and device therefore go on the existing `start   whisper resident` line
  (`:1153-1154`) instead.
- **R3. A user's own `OMARCHY_VOICE_WHISPER_MODEL` cannot be told apart
  from the package's,** because the wrapper sets it with `--set-default`.
  The never-swap rule therefore applies to `[ears] whisper_model`, the
  documented user setting. A user who sets the variable can also set
  `OMARCHY_VOICE_WHISPER_CPU_MODEL` to the same path to opt out. The
  README says so.

- **R4 (independent review, after step 8).** Two fixes:
  - **The swap now also runs when the server dies or times out before
    its port opens** (`_should_swap`, checked once more after the wait
    loop). Without it, a slow or out-of-memory start on a GPU-less machine
    returned `None`, and whisper-cli then ran turbo on the CPU, about 20 s
    per utterance. Test: `test_a_server_that_dies_saying_no_gpu_still_swaps`.
  - **`ask` writes its own log**, `RUNTIME_DIR/whisper-ask.log`, through
    a new `log` parameter on `Server.start`. It no longer empties the
    daemon's `whisper-server.log`, which `doctor` reads.

## Steps

1. **`nix/whisper-model.nix`**
   - After the `"small.en"` entry (`:51-55`), add:
     ```nix
     "large-v3-turbo-q5_0" = mkModel {
       name = "large-v3-turbo-q5_0";
       sha256 = "1qm7zxamlvac564c3270wqqqks5wc7532q3fqi01zbfmkiq22hir";
       description = "whisper.cpp large-v3-turbo, 5-bit (~574 MB) — for a GPU; ~20 s an utterance on a CPU";
     };
     ```
   - Rewrite the comment at `:57-59` to say: turbo is the default because
     the Vulkan build runs it in about 0.2 s and it hears names `base.en`
     misses (#177); on a CPU it takes about 20 s, so the package also ships
     `base.en` and the daemon switches to it when whisper finds no GPU.
     Set `default = self."large-v3-turbo-q5_0";`.
   - Edit the `.en` paragraph (`:11-14`) to say that turbo is
     multilingual, but the daemon pins English with `-l en`.

   → verify: `nix build --impure --no-link --print-out-paths --expr '(let pkgs = (builtins.getFlake (toString ./.)).inputs.nixpkgs.legacyPackages.x86_64-linux; in (pkgs.callPackage ./nix/whisper-model.nix {})).default'`
   builds, and the store path holds `ggml-large-v3-turbo-q5_0.bin` at
   574,041,195 bytes.
   Traps: `mkModel` names the file `ggml-${name}.bin`, so the name must be
   exactly `large-v3-turbo-q5_0`. A wrong hash is a build error; it does
   not fail silently.

2. **`nix/package.nix`**
   - After `whisperModel` (`:36-40`), add the argument:
     ```nix
     # What the daemon falls back to when whisper reports no GPU (#177):
     # turbo on a CPU is ~20 s an utterance, base.en ~1.3 s.
     , whisperCpuModel ? (callPackage ./whisper-model.nix { })."base.en"
     ```
   - Update the `whisperModel` comment: the default is turbo, for the GPU.
   - In `postFixup`, after the `OMARCHY_VOICE_WHISPER_MODEL` line
     (`:117-118`), add:
     ```nix
     --set-default OMARCHY_VOICE_WHISPER_CPU_MODEL \
       ${whisperCpuModel}/ggml-${whisperCpuModel.modelName}.bin \
     ```
   - In the `whisperImpl` comment (`:20-30`), drop the 1.6 s / 0.33 s
     figures for `base.en`. Say the CPU build works, and the daemon
     switches to the CPU model on its own.

   → verify: `nix build .#default`, then
   `grep -E 'WHISPER_(CPU_)?MODEL' result/bin/omarchy-voice` shows both
   paths, turbo first.
   Traps: `postFixup` is one long `wrapProgram` with backslash
   continuations, so a missing `\` silently ends the command. Check both
   lines are in the wrapper, not just that the build passed.

3. **`src/omarchy_voice/listen_local.py`: the fallback**
   - Import `RUNTIME_DIR` from `.config`, and `re`.
   - Constants, next to `MODEL_ENV` (`:31`):
     ```python
     CPU_MODEL_ENV = "OMARCHY_VOICE_WHISPER_CPU_MODEL"
     SERVER_LOG = RUNTIME_DIR / "whisper-server.log"
     NO_GPU = "whisper_backend_init_gpu: no GPU found"
     ```
     Add a module flag `_on_cpu = False`, with a one-line comment saying
     it is set once per process when the fallback fires.
   - `model_path` (`:250-253`): explicit config first, as now. Otherwise,
     if `_on_cpu` and `os.environ.get(CPU_MODEL_ENV)`, that. Otherwise
     `MODEL_ENV`.
   - Add `device_from_log(text) -> str`. It returns the name from
     `whisper_backend_init_gpu: device \d+: (\S+)`, giving `"Vulkan0"` or
     `"CPU"`. It returns `"CPU"` if `NO_GPU` is in the text, and `""` if
     neither is found.
   - Add `describe(model, device) -> str`: `"<name> on <device>"`, where
     `name` is the file name with `ggml-` and `.bin` removed, and device
     `""` reads "on an unknown device".
   - `Server.__init__` gains `model` and `device` attributes.
   - `Server.start` (`:184-211`):
     - Create the log directory: `RUNTIME_DIR.mkdir(parents=True, exist_ok=True)`.
     - Open `SERVER_LOG` with `"wb"` and pass it as `stderr=` instead of
       `DEVNULL`. Close the parent's handle after `Popen`; the child keeps
       its own.
     - Once the port answers, read the log and set `server.device` and
       `server.model`.
     - Swap if all of these hold: `NO_GPU` is in the log, config has no
       explicit `whisper_model`, `CPU_MODEL_ENV` is set and differs from
       the model in use, and `_on_cpu` is not yet set. To swap: stop the
       server, set `_on_cpu = True`, and return `cls.start(config,
       timeout)`. The flag caps this at one recursion.

   → verify: step 7's tests.
   Traps:
   - Never pass `stderr=PIPE`. Nobody drains it, so the server would
     block once the buffer fills (spec, Design).
   - `tests/_isolated.py:64` points `XDG_RUNTIME_DIR` at a temp root, and
     `RUNTIME_DIR` is computed at import. Import `_isolated` before
     `omarchy_voice` in any new test file, as the existing ones do (#99).
   - `_on_cpu` is module state, so tests must reset it in `setUp`.

4. **`src/omarchy_voice/local_engine.py:1150-1154`: the start log (R2)**
   - Replace `"start   whisper resident"` with
     `f"start   whisper resident: {listen_local.describe(self.server.model, self.server.device)}"`.
   - If `listen_local._on_cpu`, also log
     `"warn    whisper found no GPU — using <cpu model name>, not <default name> (#177)"`.
   - Leave `"start   whisper per utterance"` as it is.

   → verify: step 8 on p620, where `session.log` shows
   `whisper resident: large-v3-turbo-q5_0 on Vulkan0`.
   Traps: `self.server` can be `None`. Keep the existing ternary's
   structure.

5. **`src/omarchy_voice/cli.py:73-78`: `ask` uses the server (R1)**
   - Before transcribing: `server = listen_local.Server.start(config)`.
   - Call `listen_local.transcribe(pcm, config, server=server)` in a
     `try`/`finally` that calls `server.stop()` when `server` is not
     `None`.
   - The `started = time.monotonic()` timing stays around the transcribe
     call only, so the printed "transcribed locally in" figure does not
     include the load.

   → verify: `omarchy-voice ask` on p620 prints a transcript, and
   `$XDG_RUNTIME_DIR/omarchy-voice/whisper-server.log` is fresh.
   Traps: `ask` has its own log line format at `:79-80`. Do not change it.

6. **`src/omarchy_voice/cli.py:479-505`: `doctor`'s ears section**
   - After the `` `omarchy-voice ask` `` tick lines (around `:497-500`),
     add one line. If `SERVER_LOG` exists, print `whisper:
     {describe(model, device)} (as of the daemon's last start)`. The
     device comes from the log. The model is `listen_local.model_path(config)`,
     or the CPU model env when the log says `CPU` and that env is set and
     no `[ears] whisper_model` is configured. If the device is
     `CPU` and the CPU model env is set, add a second line: `whisper found
     no GPU, so it uses <cpu model>`. If there is no log, print `whisper:
     <model name>, device unknown until the daemon starts`.

   → verify: `omarchy-voice doctor` on p620 shows `large-v3-turbo-q5_0
   on Vulkan0`.
   Traps: `doctor` runs in a separate process from the daemon, so
   `_on_cpu` is always `False` there. Read the device from the log; never
   from the flag.

7. **`tests/test_listen_local.py`: add `GpuFallbackTests`**
   - Fake `subprocess.Popen` writes a given stderr text into the `stderr`
     file handle, and a fake socket connect succeeds.
   - Each test: `setUp` resets `listen_local._on_cpu = False`, and sets
     both `MODEL_ENV=/turbo.bin` and `CPU_MODEL_ENV=/base.bin` with
     `mock.patch.dict`.
   - Tests:
     - a. A Vulkan log → one `Popen`, on `/turbo.bin`, with
       `device == "Vulkan0"`.
     - b. A `NO_GPU` log → two `Popen` calls, the second on `/base.bin`,
       with `_on_cpu` true and the first process stopped.
     - c. A `NO_GPU` log with `Config(whisper_model="/mine.bin")` → one
       `Popen`, on `/mine.bin`.
     - d. After b, `transcribe` with no server calls `whisper-cli` with
       `-m /base.bin`.
     - e. `device_from_log` on both observed log texts quoted above.
     - f. A live test, `@skipUnless(shutil.which("whisper-server") and
       os.environ.get(CPU_MODEL_ENV))`: start the real server with
       `VK_ICD_FILENAMES=/nonexistent` and `VK_DRIVER_FILES=/nonexistent`
       on `CPU_MODEL_ENV`, and assert `NO_GPU` is in the log. This pins the
       string against the packaged binary.

   → verify: `nix develop -c pytest tests/test_listen_local.py -q`
   passes, with f skipped. Then run f against the built package:
   ```
   W=$(nix build --print-out-paths --no-link --impure --expr 'let pkgs = (builtins.getFlake (toString ./.)).inputs.nixpkgs.legacyPackages.x86_64-linux; in pkgs.whisper-cpp-vulkan')
   M=$(nix build --print-out-paths --no-link --impure --expr '(let pkgs = (builtins.getFlake (toString ./.)).inputs.nixpkgs.legacyPackages.x86_64-linux; in (pkgs.callPackage ./nix/whisper-model.nix {}))."base.en"')
   PATH=$W/bin:$PATH OMARCHY_VOICE_WHISPER_CPU_MODEL=$M/ggml-base.en.bin \
     nix develop -c pytest tests/test_listen_local.py -q -k live
   ```
   Test f must run, not skip, and pass.
   Traps: `nix flake check`'s `unit` check runs in the sandbox with no
   whisper on PATH, so f skips there by design. Do not add whisper to the
   check just for this.

8. **Docs**
   - README `:3`: "whisper.cpp transcribes it on this CPU" becomes "on
     your GPU (or CPU)".
   - README `:41` and `docs/index.html:162-164`: the disk size, from
     `nix path-info -Sh ./result` after step 2. `docs/index.html:161`:
     "whisper.cpp runs on your CPU" becomes "on your machine".
   - README `:320-330`: turbo by default on the GPU, and `base.en` taken
     automatically when whisper finds no GPU (the `whisper resident` log
     line and `doctor` show which). Mention the two overrides,
     `whisperModel` and `whisperCpuModel`. Change the example override to
     `."base.en"` and say it is for a machine without a GPU. Add R3: users
     who set `OMARCHY_VOICE_WHISPER_MODEL` themselves should also set
     `OMARCHY_VOICE_WHISPER_CPU_MODEL`, or use `[ears] whisper_model`,
     which is never swapped.
   - `share/config.example.toml:119-122`: the `whisper_model` comment says
     a model set here is used even when whisper finds no GPU.

   → verify: `grep -n "on this CPU\|runs on your CPU\|6.7 GiB" README.md docs/index.html`
   finds nothing.
   Traps: none.

9. **Real recordings (spec V3), before the PR leaves draft**
   - The owner records ten commands on p620, one file each:
     `pw-record --rate 16000 --channels 1 --format s16 rN.wav`. Include
     "Oma", Vesktop, herdr, Waybar, Hyprland, Claude Code, and a workspace
     number.
   - Write the intended text to `rN.txt`.
   - Run each model with the daemon's prompt, using `vocabulary()` from
     the built package:
     ```
     whisper-cli -m <model> -l en -nt -np --prompt "<vocabulary>" r*.wav
     ```
     Do this for `base.en` and turbo.
   - Record a table in this plan: sentence, `base.en` output, turbo
     output, names right for each.
   - **Gate:** turbo gets at least as many names right as `base.en` in
     total, and no sentence is worse. If the gate fails, stop and report;
     do not ship the default.

   Traps: the recordings stay out of the repo (voice data). Keep them in
   the scratchpad.

## Tests

- `nix develop -c pytest tests -q` passes in full.
- The live test 7f passes against the packaged binary.
- `nix flake check --print-build-logs` passes.
- On p620 (spec V4):
  - The daemon restarts, and the log shows `whisper resident:
    large-v3-turbo-q5_0 on Vulkan0`.
  - One spoken command is transcribed correctly.
  - `TIMING` shows no regression.
  - `doctor` shows the same model and device.
- Forced fallback (spec V5):
  - Add a user drop-in for `omarchy-voice.service` with
    `Environment=VK_ICD_FILENAMES=/nonexistent VK_DRIVER_FILES=/nonexistent`
    and restart.
  - The log shows the `warn    whisper found no GPU` line and
    `whisper resident: base.en on CPU`. A command is transcribed in about
    1-2 s. `doctor` says it is on the CPU.
  - Remove the drop-in and restart.
- razer (spec V6): run `doctor` over ssh, with the Hyprland environment
  exported. Record the device it reports.
- Live testing on p620 uses a user-space drop-in and the built package
  (see memory "live-testing omarchy-voice on p620"). Do not run
  nixos-rebuild.

## Rollback

- Revert the merge commit. The package goes back to `base.en` only, and
  the daemon's code no longer looks at the server log. Nothing is
  persisted: the server log lives in the runtime directory and is
  rewritten on each start.
- Per user, with no revert: set `[ears] whisper_model` to a `base.en`
  path, or override `whisperModel = (…/whisper-model.nix { })."base.en"` in Nix.
