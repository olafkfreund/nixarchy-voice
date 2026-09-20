{
  description = "Oma — OpenAI Realtime voice control for Nixarchy (Omarchy on NixOS)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    systems.url = "github:nix-systems/default-linux";
    # Only so checks can evaluate the Home Manager module this repo ships. A
    # module nothing evaluates is a module nothing checks.
    home-manager.url = "github:nix-community/home-manager";
    home-manager.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { self, nixpkgs, systems, home-manager }:
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
      #     inputs.nixarchy-voice.lib.${pkgs.stdenv.hostPlatform.system}.withVoiceRoutes
      #       (pkgs.extend inputs.nixarchy.overlays.default).omarchy;
      lib = eachSystem (pkgs: {
        withVoiceRoutes = pkgs.callPackage ./nix/with-voice-routes.nix {
          omarchy-voice = self.packages.${pkgs.stdenv.hostPlatform.system}.omarchy-voice;
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
            (pkgs.python3.withPackages (ps: with ps; [ websockets mcp claude-agent-sdk pytest ]))
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

      checks = eachSystem (pkgs:
        let
          # The generated config.toml, for a home with this module and nothing else.
          generated = options: (home-manager.lib.homeManagerConfiguration {
            inherit pkgs;
            modules = [
              self.homeModules.omarchy-voice
              {
                home = { username = "check"; homeDirectory = "/home/check"; stateVersion = "24.11"; };
                programs.omarchy-voice = { enable = true; barWidget = false; orb = false; } // options;
              }
            ];
          }).config.xdg.configFile."omarchy-voice/config.toml".source or null;
        in
        {
        # The whole suite. It stubs hyprctl and omarchy rather than calling
        # them, so nothing here needs a live Wayland session.
        unit = pkgs.runCommand "omarchy-voice-tests"
          {
            nativeBuildInputs = [
              # claude-agent-sdk must be real here, not just in the built
              # package: claude_backend.py imports PermissionResultAllow /
              # PermissionResultDeny behind a try/except with local
              # stand-ins so the module stays readable without the SDK
              # installed, but the SDK isinstance-checks whatever
              # can_use_tool returns, so those stand-ins are never a
              # supported runtime path. Missing the dependency here would
              # let the tests exercise only the stand-ins.
              (pkgs.python3.withPackages (ps: with ps; [ websockets mcp claude-agent-sdk pytest ]))
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

        # #17: the desktop is handed over only when someone says so, and the
        # option is the only thing that can say it from a configuration.
        hm-desktop-control = pkgs.runCommand "omarchy-voice-hm-desktop-control" { } ''
          grep -q 'desktop_control = true' ${generated { desktopControl = true; }}
          # A home with other settings and no opt-in must carry no such key:
          # with no settings at all there is no file to read, which proves nothing.
          ! grep -q 'desktop_control' ${generated { settings.ears.barge_in = true; }}
          touch $out
        '';
      });

      formatter = eachSystem (pkgs: pkgs.nixfmt);
    };
}
