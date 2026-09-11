{ lib
, python3Packages
, makeWrapper
, hyprland
, libxkbcommon
, wtype
, ydotool
, grim
, tesseract
, wl-clipboard
, libnotify
, pipewire
, pulseaudio
, tmux
, espeak-ng
  # Set to the omarchy package if you want a build-time default; normally the
  # session's own OMARCHY_PATH is used and this stays null.
, omarchy ? null
, extraRuntimeInputs ? [ ]
}:

python3Packages.buildPythonApplication rec {
  pname = "omarchy-voice";
  version = "0.3.0";
  pyproject = true;

  src = lib.cleanSource ../.;

  build-system = [ python3Packages.setuptools ];
  dependencies = [ python3Packages.websockets ];

  nativeBuildInputs = [ makeWrapper ];

  # The daemon shells out to these by name. On NixOS nothing is on a global
  # PATH, and every one of them fails soft — a missing wtype means the model
  # silently cannot type, with no error anywhere. Put them in the wrapper.
  runtimeInputs = [
    wtype ydotool grim tesseract wl-clipboard libnotify
    pipewire pulseaudio tmux espeak-ng
  ] ++ extraRuntimeInputs;

  # keys.py resolves keysyms through libxkbcommon with ctypes. Absent, it falls
  # back to passing every name through unverified, so "Enter" reaches Hyprland
  # instead of being refused — a silent downgrade, not a crash.
  postFixup = ''
    wrapProgram $out/bin/omarchy-voice \
      --prefix PATH : ${lib.makeBinPath runtimeInputs} \
      --prefix LD_LIBRARY_PATH : ${lib.makeLibraryPath [ libxkbcommon ]} \
      --set-default OMARCHY_VOICE_HL_STUB ${hyprland}/share/hypr/stubs/hl.meta.lua \
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
    description = "Speech-to-speech voice control for Omarchy on NixOS";
    homepage = "https://github.com/olafkfreund/nixarchy-voice";
    license = lib.licenses.mit;
    mainProgram = "omarchy-voice";
    platforms = lib.platforms.linux;
  };
}
