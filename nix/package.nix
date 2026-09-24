{ lib
, ai-mirror-input
, python3Packages
, makeWrapper
, hyprland
, libxkbcommon
, wtype
, grim
, tesseract
, wl-clipboard
, libnotify
, pipewire
, pulseaudio
, tmux
, playerctl
, espeak-ng
, piper-tts
, whisper-cpp
, whisper-cpp-vulkan
  # Which whisper.cpp transcribes with. Vulkan by default: it is the same
  # binary and the same transcript, but it runs on the GPU instead of
  # competing with everything else for the CPU. Measured on a Radeon RX 7900
  # XT against 3.66s of speech -- CPU 1.6s, Vulkan 0.33s once its shaders are
  # compiled (the first run pays ~1.2s for that, once per boot).
  #
  # It costs nothing in the closure: 1009 MiB against 1.0 GiB, because it
  # links the system's Vulkan loader rather than carrying a driver.
  #
  # Override with `whisperImpl = pkgs.whisper-cpp` on a machine with no usable
  # Vulkan device -- a VM, a headless box, or an old card. The CPU build is
  # not a downgrade in accuracy, only in speed.
, whisperImpl ? whisper-cpp-vulkan
, ffmpeg
, libsecret
  # The model the two local listeners share: `omarchy-voice ask` (dictation)
  # and the wake word. Override with another from nix/whisper-model.nix --
  # "tiny.en" is enough for a wake word alone and a third of the size.
, whisperModel ? (callPackage ./whisper-model.nix { }).default
  # The voice piper speaks with. Override to pick another from
  # nix/piper-voice.nix, or point it at any rhasspy/piper-voices download.
, piperVoice ? (callPackage ./piper-voice.nix { }).default
, callPackage
  # Set to the omarchy package if you want a build-time default; normally the
  # session's own OMARCHY_PATH is used and this stays null.
, omarchy ? null
, extraRuntimeInputs ? [ ]
}:

python3Packages.buildPythonApplication rec {
  pname = "omarchy-voice";
  version = "2.0.0";
  pyproject = true;

  src = lib.cleanSource ../.;

  build-system = [ python3Packages.setuptools ];
  dependencies = with python3Packages; [ mcp claude-agent-sdk ];

  nativeBuildInputs = [ makeWrapper ];

  # The daemon shells out to these by name. On NixOS nothing is on a global
  # PATH, and every one of them fails soft — a missing wtype means the model
  # silently cannot type, with no error anywhere. Put them in the wrapper.
  runtimeInputs = [
    wtype
    # Clicking and the wheel. Wayland's zwp_virtual_keyboard_v1 and
    # zwlr_virtual_pointer_v1, so no /dev/uinput and no root daemon -- and
    # closing it releases whatever it was holding, which ydotool could not do
    # (#30).
    ai-mirror-input
    grim
    tesseract
    wl-clipboard
    libnotify
    pipewire
    pulseaudio
    tmux
    # MPRIS: system_query(media) and media_control shell out to it (#73).
    playerctl
    espeak-ng
    piper-tts
    whisperImpl
    # ElevenLabs returns mp3 (raw PCM is a Pro-tier format), so without ffmpeg
    # the cloud voice decodes nothing and every reply silently falls back to
    # piper — the good voice appearing never to work rather than reporting why.
    ffmpeg
    # secret-tool, where the ElevenLabs API key lives. Absent, the only place
    # left to read a key from is a plaintext export in a shell profile.
    libsecret
  ] ++ extraRuntimeInputs;

  # keys.py resolves keysyms through libxkbcommon with ctypes. Absent, it falls
  # back to passing every name through unverified, so "Enter" reaches Hyprland
  # instead of being refused — a silent downgrade, not a crash.
  postFixup = ''
    wrapProgram $out/bin/omarchy-voice \
      --prefix PATH : ${lib.makeBinPath runtimeInputs} \
      --prefix LD_LIBRARY_PATH : ${lib.makeLibraryPath [ libxkbcommon ]} \
      --set-default OMARCHY_VOICE_HL_STUB_FALLBACK \
        ${hyprland}/share/hypr/stubs/hl.meta.lua \
      --set-default OMARCHY_VOICE_PIPER_MODEL \
        ${piperVoice}/${piperVoice.voiceName}.onnx \
      --set-default OMARCHY_VOICE_WHISPER_MODEL \
        ${whisperModel}/ggml-${whisperModel.modelName}.bin \
      ${lib.optionalString (piperVoice.attribution or null != null)
        "--set-default OMARCHY_VOICE_ATTRIBUTION ${lib.escapeShellArg piperVoice.attribution}"} \
      ${lib.optionalString (omarchy != null)
        "--set-default OMARCHY_PATH ${omarchy}"}
  '';

  # The suite reaches the real desktop (hyprctl, desktop entries, a loopback
  # websocket server). Run it with `nix develop` / `nix flake check` on a live
  # session instead of in the sandbox.
  doCheck = false;

  # `omarchy voice ...` routes plus the Quickshell plugins, for the module and
  # for anyone wiring them up by hand.
  postInstall = ''
    install -Dm755 -t $out/libexec/omarchy-voice omarchy/bin/omarchy-voice*
    # These are named omarchy-voice* themselves, so they must never resolve the
    # real binary through PATH — they would find each other.
    substituteInPlace $out/libexec/omarchy-voice/* --replace-fail "@out@" "$out"
    mkdir -p $out/share/omarchy-voice
    cp -r plugin $out/share/omarchy-voice/plugins
    install -Dm644 share/config.example.toml \
      $out/share/omarchy-voice/config.example.toml
    install -Dm644 share/echo-cancel.conf \
      $out/share/omarchy-voice/echo-cancel.conf
  '';

  meta = {
    description = "Voice control for Omarchy on NixOS";
    homepage = "https://github.com/olafkfreund/nixarchy-voice";
    license = lib.licenses.mit;
    mainProgram = "omarchy-voice";
    platforms = lib.platforms.linux;
  };
}
