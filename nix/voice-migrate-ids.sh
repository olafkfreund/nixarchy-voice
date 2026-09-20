# One-time migration of the voice plugin ids (#18).
#
# The ids gained an `olafkfreund.` prefix. A plugin the user had ENABLED is
# named in shell.json under its old id, which now names nothing, so the bar
# would silently lose the widget. shell.json is never edited here: the shell
# rewrites that file from memory, so a second writer loses updates
# (nixarchy#766). Everything goes through omarchy's own plugin commands.
#
# The marker is written only when every step succeeded, so a half-done
# migration is retried at the next login rather than recorded as done.

state="${XDG_STATE_HOME:-$HOME/.local/state}/omarchy-voice"
marker="$state/ids-migrated"
[ -e "$marker" ] && exit 0

# Without omarchy's commands there is nothing to migrate through. Next login.
command -v omarchy >/dev/null || exit 0
command -v omarchy-plugin-list >/dev/null || exit 0

list=$(omarchy-plugin-list --json 2>/dev/null) || exit 0

ok=1
migrate() {
  local old=$1 new=$2 section=$3 out
  # An old id that was off stays off: it is a choice, not a leftover.
  jq -e --arg id "$old" 'any(.[]; .id == $id and .enabled)' <<<"$list" >/dev/null || return 0
  if ! out=$(omarchy plugin disable "$old" 2>&1); then
    printf '%s: %s\n' "$old" "$out" | systemd-cat -t omarchy-voice-migrate-ids
    ok=0
    return 0
  fi
  if ! out=$(omarchy plugin enable "$new" "$section" 2>&1); then
    printf '%s: %s\n' "$new" "$out" | systemd-cat -t omarchy-voice-migrate-ids
    ok=0
  fi
}

migrate voice.indicator olafkfreund.voice-indicator right
migrate voice.orb olafkfreund.voice-orb plugins

if [ "$ok" = 1 ]; then
  mkdir -p "$state"
  : >"$marker"
fi
