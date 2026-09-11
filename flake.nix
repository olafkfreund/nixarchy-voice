{
  description = "Oma — OpenAI Realtime voice control for Nixarchy (Omarchy on NixOS)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    systems.url = "github:nix-systems/default-linux";
  };

  outputs = { self, nixpkgs, systems }:
    let
      eachSystem = f:
        nixpkgs.lib.genAttrs (import systems)
          (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = eachSystem (pkgs: rec {
        omarchy-voice = pkgs.callPackage ./nix/package.nix { };
        default = omarchy-voice;
      });

      # `omarchy voice ...` — omarchy finds subcommands by listing its own
      # directory, so the routes have to be built into the Omarchy package.
      # Point programs.nixarchy.package at the result:
      #
      #   programs.nixarchy.package =
      #     inputs.nixarchy-voice.lib.${pkgs.system}.withVoiceRoutes
      #       (pkgs.extend inputs.nixarchy.overlays.default).omarchy;
      lib = eachSystem (pkgs: {
        withVoiceRoutes = pkgs.callPackage ./nix/with-voice-routes.nix {
          omarchy-voice = self.packages.${pkgs.system}.omarchy-voice;
        };
      });

      homeModules = {
        omarchy-voice = import ./nix/hm-module.nix self;
        default = self.homeModules.omarchy-voice;
      };

      overlays.default = final: prev: {
        omarchy-voice = final.callPackage ./nix/package.nix { };
      };

      devShells = eachSystem (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: with ps; [ websockets mcp pytest ]))
            pkgs.wtype pkgs.ydotool pkgs.grim pkgs.tesseract
            pkgs.wl-clipboard pkgs.libnotify pkgs.pipewire pkgs.pulseaudio
          ];
          # Same three environment facts the wrapper sets, so `python -m
          # omarchy_voice` in the shell behaves like the installed binary.
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.libxkbcommon ];
          OMARCHY_VOICE_HL_STUB =
            "${pkgs.hyprland}/share/hypr/stubs/hl.meta.lua";
          shellHook = ''
            export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
            echo "omarchy-voice dev shell — pytest tests"
          '';
        };
      });

      checks = eachSystem (pkgs: {
        # The whole suite. It stubs hyprctl and omarchy rather than calling
        # them, so nothing here needs a live Wayland session.
        unit = pkgs.runCommand "omarchy-voice-tests"
          {
            nativeBuildInputs = [
              (pkgs.python3.withPackages (ps: with ps; [ websockets mcp pytest ]))
              # Several tests assert on what happens when the screen is asleep
              # or the session locked. Without these on PATH they instead hit
              # the "not installed" branch and assert on the wrong message.
              pkgs.grim pkgs.tesseract pkgs.wtype pkgs.wl-clipboard
            ];
            LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.libxkbcommon ];
          } ''
          cp -r ${pkgs.lib.cleanSource ./.} src-tree
          chmod -R +w src-tree
          cd src-tree
          export PYTHONPATH=$PWD/src HOME=$TMPDIR
          pytest tests -q
          touch $out
        '';
      });

      formatter = eachSystem (pkgs: pkgs.nixfmt);
    };
}
