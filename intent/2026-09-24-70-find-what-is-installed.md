---
status: draft
issue: 70
author: olafkfreund
---

# Intent: the assistant must be able to find what is installed, by the name a person uses

Closes #70.

## Problem

The user wants to say "open Zed" or "use Obsidian for this" and have the
assistant find the tool. Today it cannot see most of what is installed, so it
guesses.

### What the model is shown

`capabilities.installed_apps(limit=28)` (`src/omarchy_voice/capabilities.py:425-452`)
takes the first 28 desktop entries in directory order. Measured on this
machine:

| | Count |
|---|---|
| `.desktop` files | 535 |
| hidden (`NoDisplay=true`) | 145 |
| launchable, unique by name | **334** |
| shown to the model | **28**, and 15 of the 334 are Steam or Proton runtimes, which sort first |

Not one of Firefox, Obsidian, Files, Vesktop, Spotify, Zed, Visual Studio
Code, Slack, 1Password or OBS Studio is in the 28. All of them are installed.

Nothing else in the prompt covers installed software. There is no index of the
3,817 commands on PATH, the 151 user service units, or the 19 MCP servers
configured for Claude Code. The coding agents are a hard-coded 4-row table
(`capabilities.py:785-794`).

### What that costs, measured

Three typed requests, in dry run so nothing launched
(`omarchy-voice -n say --no-confirm …`):

| Said | What the model did | Time |
|---|---|---|
| "open obsidian" | guessed `omarchy launch or focus obsidian obsidian`, which happens to be valid | 9.6 s |
| "open the file manager" | a `ToolSearch` round trip, then `omarchy launch nautilus`, which is valid | 7.6 s |
| "open zed" | `launch_app "zed"`, refused because there is no entry called `zed`; it then proposed `omarchy launch zed`, **which does not exist** | 9.7 s |

Zed is the clearest case. The user says **"zed"**, the desktop id is
**`dev.zed.Zed`**, and the command is **`zeditor`**. The model was shown none
of these, and all three are in one local file:
`dev.zed.Zed.desktop` has `Name=Zed` and `Exec=zeditor %U`. The same file type
answers "the file manager": Nautilus declares
`Keywords=folder;manager;explore;disk;filesystem;nautilus;`.

`launch_app` already refuses a desktop id that does not exist (`tools.py:2066-2070`),
so a wrong guess fails loudly rather than silently. But each wrong guess is a
whole model turn (1.5-7.6 s, intent #23), and the repair is another guess.

### The shape of a fix already exists here

`omarchy_help` is a search tool over the Omarchy CLI (`tools.py`,
`_tool_omarchy_help` → `capabilities.search_commands`). The model asks by
topic and gets back exact, runnable routes. Nothing equivalent exists for apps,
commands or services.

### A correction to the note left on #70

That note suggested Keystroke's `descriptions.json` could be the index. It
cannot: its 618 entries are 333 menu items, 205 hotkeys and **80 apps written
for a stock Omarchy install. Only 32 of those 80 exist on this machine**, out of
334 launchable apps. The menu and hotkey descriptions may still be useful for
Omarchy's own actions. For installed software, this machine's own desktop
entries (`Name`, `GenericName`, `Keywords`, `Comment`, `Exec`, `Actions`) are
the better source.

## Proposed outcome

- Asked for an installed app by the name a person uses ("zed", "the file
  manager", "my password manager", "obsidian"), the assistant finds the
  exact thing to run, and it runs, on the first try.
- The model is no longer shown a truncated list chosen by directory order.
  What it can look up covers everything launchable on this machine.
- An ambiguous name ("code": VS Code or Claude Code?) comes back as a
  choice, not a guess.
- A request for something that is not installed is answered as not installed,
  not as an invented command.
- Looking something up costs at most one tool round trip, and the common case
  costs none beyond the launch itself.

## Affected users and systems

- Every engine and entry shape, since all share one `Executor` and one
  manifest: voice (local and realtime), typed `say`, and the MCP server.
- `src/omarchy_voice/capabilities.py` (`installed_apps`, the manifest),
  `tools.py` (a lookup tool beside `omarchy_help`, and `launch_app`), the
  persona in `persona.py` where it steers the model away from the shell, and
  the MCP tool list in `mcp_server.py`.
- #71 (answering common commands without the model) builds on this index, so
  its design constrains this one.

## Constraints

- **Local only.** The index is built from this machine's files and nothing is
  sent anywhere to build or search it.
- **No runtime downloads and no committed binaries.** This is the exact reason
  nixarchy-menu#2 is removing Smart Match. Anything beyond the standard library
  comes from nixpkgs through the flake.
- **The cached prompt prefix stays stable.** A large, changing app list in the
  system prompt is what #69 just moved away from. Whatever the model sees
  every turn must be small, and must not change with every package install.
- **Launching stays behind the policy gate.** Finding something must not become
  a way around the confirm/deny rules. Commands found on PATH are not
  automatically runnable.
- **Hidden entries stay hidden.** `NoDisplay=true`, and entries for other
  desktops (`OnlyShowIn`/`NotShowIn`), are not offered as apps.
- A stale index is worse than a slow one. Installing or removing an app must be
  reflected without restarting the daemon, or at worst on the next start.

## Open questions

1. **How far beyond desktop entries?** Desktop entries answer "open X". PATH
   commands answer "use X to do Y". That is what the user asked for, but it
   means 3,817 names, most of them libraries' helpers. User units and MCP
   servers answer "is my X running" and "what can you reach". Start with
   desktop entries and their `Actions=`, and add PATH commands (with a tldr
   or `--help` summary) in a second step? Or all at once?
2. **Lookup tool, or shortlist in the prompt?** Either the model calls
   `find(query)` like `omarchy_help` (one round trip, a stable prompt), or
   the daemon matches the utterance before the model is asked and puts the top
   few candidates into the turn, the way #69 puts the desktop there (no round
   trip, a few dozen tokens). The second overlaps with #71's router. Which, or
   the tool now and the shortlist with #71?
3. **Lexical or semantic matching?** Local desktop metadata already carries
   the words people use (`Keywords=folder;manager;…`, `GenericName`). Lexical
   matching (`rapidfuzz`, in nixpkgs) may be enough. Model2Vec, as in
   Keystroke, would need a Nix-built model. Proposal: lexical first, measured on
   a fixed set of real requests, and semantic only if that set shows misses.
