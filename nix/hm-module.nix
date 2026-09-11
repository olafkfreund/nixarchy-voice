# Home Manager module. Voice control is a per-user thing: a user service, a
# user config file, a user API key, a plugin in the user's own plugin dir.
# Nothing here needs root, so nothing here is a NixOS module.
self:
{ config, lib, pkgs, ... }:
let
  cfg = config.programs.omarchy-voice;
  tomlFormat = pkgs.formats.toml { };
in
{
  options.programs.omarchy-voice = {
    enable = lib.mkEnableOption "Oma, voice control for Nixarchy";

    package = lib.mkPackageOption self.packages.${pkgs.system} "omarchy-voice" { };

    settings = lib.mkOption {
      type = tomlFormat.type;
      default = { };
      example = lib.literalExpression ''
        {
          realtime.voice = "marin";
          ears.barge_in = true;
          hands.allow_shell = false;
        }
      '';
      description = ''
        Contents of {file}`~/.config/omarchy-voice/config.toml`.
        See `config.example.toml` in the package for every key.
      '';
    };

    environmentFile = lib.mkOption {
      type = with lib.types; nullOr (either path str);
      default = null;
      example = "/run/agenix/openai-api-key";
      description = ''
        File holding `OPENAI_API_KEY=sk-...`, read by the user service.

        Keep this out of the Nix store: a store path is world-readable and
        lands in every backup of the machine. Point it at an agenix/sops
        secret, or at a hand-written {file}`~/.config/omarchy-voice/env`
        with mode 600.
      '';
    };

    service.enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Run the daemon as a systemd user service.";
    };

    barWidget = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Link the listening indicator into
        {file}`~/.config/omarchy/plugins`. Placing it on the bar is still
        `omarchy bar put voice.indicator --section right` — that writes to
        your mutable {file}`shell.json`, which this module does not own.
      '';
    };

    orb = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Also link the on-screen listening orb panel plugin.";
    };

    keybinding = lib.mkOption {
      type = with lib.types; nullOr str;
      default = "SUPER + SHIFT + V";
      description = ''
        Printed as the snippet to paste into
        {file}`~/.config/hypr/bindings.lua`. Null prints nothing.

        Deliberately not written for you: bindings.lua is yours and Hyprland
        reads exactly one of them, so a module that owned it would fight
        every other thing you bind.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    home.packages = [ cfg.package ];

    xdg.configFile = lib.mkMerge [
      (lib.mkIf (cfg.settings != { }) {
        "omarchy-voice/config.toml".source =
          tomlFormat.generate "omarchy-voice-config.toml" cfg.settings;
      })
      (lib.mkIf cfg.barWidget {
        "omarchy/plugins/voice.indicator".source =
          "${cfg.package}/share/omarchy-voice/plugins/voice.indicator";
      })
      (lib.mkIf cfg.orb {
        "omarchy/plugins/voice.orb".source =
          "${cfg.package}/share/omarchy-voice/plugins/voice.orb";
      })
    ];

    systemd.user.services.omarchy-voice = lib.mkIf cfg.service.enable {
      Unit = {
        Description = "Oma — voice control for Nixarchy";
        PartOf = [ "graphical-session.target" ];
        After = [ "graphical-session.target" "pipewire.service" ];
        # A missing key is not a transient fault. Without the limit the daemon
        # restarts every 3 s forever and fills the journal.
        StartLimitIntervalSec = 60;
        StartLimitBurst = 3;
      };
      Service = {
        Type = "simple";
        ExecStart = "${lib.getExe cfg.package} run";
        EnvironmentFile = lib.mkIf (cfg.environmentFile != null)
          [ "-${toString cfg.environmentFile}" ];
        Restart = "on-failure";
        RestartSec = 3;
        # Not ProtectSystem=strict: it needs the Wayland and PipeWire sockets
        # in XDG_RUNTIME_DIR, and to exec desktop programs out of the store.
        PrivateTmp = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictSUIDSGID = true;
        NoNewPrivileges = true;
      };
      Install.WantedBy = [ "graphical-session.target" ];
    };

    warnings = lib.optional (cfg.environmentFile == null) ''
      programs.omarchy-voice: no environmentFile set. Put
      OPENAI_API_KEY=sk-... in ~/.config/omarchy-voice/env (chmod 600) by
      hand, or the daemon will not start — a key exported in your shell does
      not reach a systemd user unit.
    '' ++ lib.optional (cfg.keybinding != null) ''
      programs.omarchy-voice: add this to ~/.config/hypr/bindings.lua —
        if o.cmd_present("omarchy-voice") then
          o.bind("${cfg.keybinding}", "Toggle voice control", "omarchy-voice listen toggle")
        end
    '';
  };
}
