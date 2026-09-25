{
  description = "Oma — voice control for Nixarchy (Omarchy on NixOS)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    systems.url = "github:nix-systems/default-linux";
    # Only so checks can evaluate the Home Manager module this repo ships. A
    # module nothing evaluates is a module nothing checks.
    home-manager.url = "github:nix-community/home-manager";
    home-manager.inputs.nixpkgs.follows = "nixpkgs";
    # The Wayland virtual-input helper, and only that: ai-mirror exports
    # `ai-mirror-input` as its own package, so this pulls in a small C
    # derivation over wayland/libxkbcommon/wlr-protocols and none of the MCP
    # server, the Python or PyGObject. It replaces ydotool, which needed
    # /dev/uinput and a root daemon and could not release a held key (#30).
    ai-mirror.url = "github:olafkfreund/ai-mirror";
    ai-mirror.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { self, nixpkgs, systems, home-manager, ai-mirror }:
    let
      eachSystem = f:
        nixpkgs.lib.genAttrs (import systems)
          (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = eachSystem (pkgs: rec {
        omarchy-voice = pkgs.callPackage ./nix/package.nix {
          ai-mirror-input = ai-mirror.packages.${pkgs.system}.ai-mirror-input;
        };
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
        omarchy-voice = final.callPackage ./nix/package.nix {
          ai-mirror-input = ai-mirror.packages.${final.system}.ai-mirror-input;
        };
      };

      devShells = eachSystem (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: with ps; [ mcp claude-agent-sdk pytest pygobject3 ]))
            pkgs.wtype pkgs.grim pkgs.tesseract
            ai-mirror.packages.${pkgs.system}.ai-mirror-input
            pkgs.wl-clipboard pkgs.libnotify pkgs.pipewire pkgs.pulseaudio
            # tools/live_check.py talks to a throwaway VM whose ssh is
            # password-authenticated only (omarchy@localhost:2222, no keys).
            pkgs.sshpass pkgs.openssh
          ];
          # Same three environment facts the wrapper sets, so `python -m
          # omarchy_voice` in the shell behaves like the installed binary.
          LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.libxkbcommon ];
          # a11y.py and T16 need the Atspi typelib (#91), as the wrapper sets it.
          GI_TYPELIB_PATH = pkgs.lib.makeSearchPath "lib/girepository-1.0"
            [ pkgs.at-spi2-core pkgs.glib.out pkgs.gobject-introspection ];
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
          inherit (pkgs) lib;
          # A home with this module and nothing else. `extra` adds modules, so a
          # check can stand in for nixarchy without depending on it.
          home = { options ? { }, extra ? [ ] }: (home-manager.lib.homeManagerConfiguration {
            inherit pkgs;
            modules = [
              self.homeModules.omarchy-voice
              {
                home = { username = "check"; homeDirectory = "/home/check"; stateVersion = "24.11"; };
                programs.omarchy-voice = { enable = true; barWidget = false; orb = false; } // options;
              }
            ] ++ extra;
          }).config;
          generated = options: (home { inherit options; }).xdg.configFile."omarchy-voice/config.toml".source or null;
          # The shape of nixarchy's own option, declared here so the module can
          # be checked against it without nixarchy as an input.
          nixarchyStub = { lib, ... }: {
            options.programs.nixarchy.plugins = lib.mkOption {
              type = lib.types.attrsOf (lib.types.submodule { options.src = lib.mkOption { type = lib.types.path; }; });
              default = { };
            };
          };
          registered = extra: home { options = { barWidget = true; }; inherit extra; };
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
              (pkgs.python3.withPackages (ps: with ps; [ mcp claude-agent-sdk pytest pygobject3 ]))
              # Several tests assert on what happens when the screen is asleep
              # or the session locked. Without these on PATH they instead hit
              # the "not installed" branch and assert on the wrong message.
              pkgs.grim pkgs.tesseract pkgs.wtype pkgs.wl-clipboard
              # test_migration_hook runs the shipped post-boot script for real,
              # and it reads omarchy-plugin-list's JSON with jq.
              pkgs.jq
            ];
            LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [ pkgs.libxkbcommon ];
            # a11y.py and T16 need the Atspi typelib (#91), as the wrapper sets it.
            GI_TYPELIB_PATH = pkgs.lib.makeSearchPath "lib/girepository-1.0"
              [ pkgs.at-spi2-core pkgs.glib.out pkgs.gobject-introspection ];
            # hypr_dispatch validates every dispatcher name against Hyprland's
            # own LuaLS stub, and refuses everything when it cannot find one --
            # falling open there would hand back the hole that check exists to
            # close (#22). So the sandbox needs the real stub, for the same
            # reason grim and wtype are on PATH above: without it the tests
            # assert on "not installed" instead of on the behaviour. This is
            # the path package.nix bakes in at postFixup.
            OMARCHY_VOICE_HL_STUB =
              "${pkgs.hyprland}/share/hypr/stubs/hl.meta.lua";
          } ''
          cp -r ${pkgs.lib.cleanSource ./.} src-tree
          chmod -R +w src-tree
          cd src-tree
          export PYTHONPATH=$PWD/src HOME=$TMPDIR
          pytest tests -q
          touch $out
        '';

        # #18: a plugin nixarchy installs is validated and reconciled by it, so
        # the module registers there when it can and links by hand when it cannot.
        hm-plugin-registration =
          let
            withNixarchy = registered [ nixarchyStub ];
            alone = registered [ ];
          in
          pkgs.runCommand "omarchy-voice-hm-plugin-registration" { } ''
            ${lib.optionalString (!(withNixarchy.programs.nixarchy.plugins ? "olafkfreund.voice-indicator"))
              "echo 'not registered through programs.nixarchy.plugins'; exit 1"}
            ${lib.optionalString (withNixarchy.xdg.configFile ? "omarchy/plugins/olafkfreund.voice-indicator")
              "echo 'linked by hand as well as registered'; exit 1"}
            ${lib.optionalString (!(alone.xdg.configFile ? "omarchy/plugins/olafkfreund.voice-indicator"))
              "echo 'no fallback link without nixarchy'; exit 1"}
            ${lib.optionalString (alone.warnings != [ ])
              "echo 'evaluating with no API key warned: ${lib.concatStringsSep " / " alone.warnings}'; exit 1"}
            touch $out
          '';

        # Directory name must equal the manifest id: omarchy-plugin-validate
        # refuses otherwise, and the shell would not find the plugin at all.
        plugin-ids = pkgs.runCommand "omarchy-voice-plugin-ids" { nativeBuildInputs = [ pkgs.jq ]; } ''
          for dir in ${./plugin}/*/; do
            name=$(basename "$dir")
            id=$(jq -r .id "$dir/manifest.json")
            [ "$id" = "$name" ] || { echo "$name: manifest id is $id"; exit 1; }
            case "$id" in olafkfreund.*) ;; *) echo "$id: not prefixed"; exit 1 ;; esac
          done
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
