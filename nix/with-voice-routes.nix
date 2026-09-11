# Add the `omarchy voice ...` routes to an Omarchy package.
#
# `bin/omarchy` finds its subcommands by listing the directory its own file is
# in — no PATH, no plugin directory, no environment override, and fourteen
# call sites that assume it. So a route is reachable only if it physically
# sits next to the omarchy binary, which on NixOS is a read-only store path.
#
# nixarchy's own `nixarchy-*` commands get there by being installed into
# share/omarchy/bin during omarchy's build, and bin/ is a symlink farm built
# over that directory. This does the same thing from the outside, so nothing
# in nixarchy has to know voice control exists.
{ lib, omarchy-voice }:

omarchy:

omarchy.overrideAttrs (old: {
  postInstall = (old.postInstall or "") + ''
    # share/omarchy/bin is the real directory; bin/ is the symlink farm over
    # it that omarchy's own installPhase built. A route needs an entry in
    # both: the farm is what puts it on PATH, and the real directory is what
    # `omarchy commands` lists.
    for route in ${omarchy-voice}/libexec/omarchy-voice/*; do
      name=$(basename "$route")
      if [ -e "$out/share/omarchy/bin/$name" ]; then
        echo "omarchy already ships a $name — refusing to overwrite it" >&2
        exit 1
      fi
      install -Dm755 "$route" "$out/share/omarchy/bin/$name"
      ln -s "$out/share/omarchy/bin/$name" "$out/bin/$name"
    done
  '';

  passthru = (old.passthru or { }) // { voiceRoutes = true; };
})
