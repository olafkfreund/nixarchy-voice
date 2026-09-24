---
status: approved
issue: 82
author: olafkfreund
---

# Intent: find an installed command-line tool by what it does, without finding becoming running

Closes #82.

## Problem

The user's goal for this project: *"I want to be able to ask for something and
use the specific tools we have installed on the system to make that happen."*
#70 did this for desktop apps (`capabilities.app_index` / `find_apps`,
`find_app`, and name resolution in `launch_app`). Nothing does it for the
commands on PATH. The model cannot look them up, so it guesses from its
training data what is probably installed.

### What PATH looks like on this machine

Measured on 2026-09-24, from the running daemon's own PATH
(`/proc/<pid>/environ` of `omarchy-voice.service`):

| | Count |
|---|---|
| PATH directories | 43 |
| unique executables | **3,769** |
| `omarchy-*` / `nixarchy-*` (already covered by `omarchy_help`) | 456 / 36 |
| with a tldr page (`common` or `linux`) | **1,190** |
| with a man page (`man1` or `man8`) | 1,685 |
| with either | 1,922 |
| with neither, excluding `omarchy-*`/`nixarchy-*` | 1,329 |

Helpers cannot be separated out by name. Only 4 names start with a dot or
end in `-wrapped`, 18 end in `-config`, 22 end in `-unwrapped` and 22 end in
a version number. The 1,329 undocumented commands mix helpers
(`clang-apply-replacements-unwrapped`, `gtk-update-icon-cache`, `kritarunner`,
`hp-doctor`) with real tools (`nix-locate`, `qFlipper-cli`, `serie`). What
does separate them is whether a command is documented. Every tool a person
would name to the assistant has a tldr page and almost all have a man page:
`ffmpeg`, `jq`, `rg`, `fd`, `yt-dlp`, `magick`, `pandoc`, `zoxide`, `gh`,
`curl`, `wl-copy`, `grim` and `hyprpicker` have both, and `btop` has tldr only.

### What tldr and man give, and what they cost

- `tldr` is the Python client, 3.4.4 (`/run/current-system/sw/bin/tldr`). Its
  pages come from a **runtime download** in `~/.cache/tldr/pages`, last
  fetched 2026-07-19: 7,383 pages across platforms, 6,568 for `common` and
  `linux`. That cache did not come from nixpkgs.
- `tldr --list` takes 0.39 s. **`tldr jq` takes 1.7 s**, and 0.17 s with the
  network unshared (`unshare -rn`). Because the cache is older than the
  client's maximum age, every lookup through the CLI makes a network call.
- Reading the page files directly is effectively free: the first `> `
  description line of all 1,190 pages took **0.03 s** in Python.
- Some pages are aliases. `mogrify`'s page says only "This command is an alias
  of `magick mogrify`". `vipsthumbnail` is installed and has no page.
- **`apropos`/`whatis` return nothing.** `man -k .` answers "nothing
  appropriate" because the whatis database is not generated on this machine.
  The man page files are there (4,138 names in `man1`/`man8` across 6 manpath
  directories), but only as files, with no index over them.

### What the log shows

`~/.local/state/omarchy-voice/session.log` has 3,046 lines and 120 heard
requests. Few of them needed a command-line tool:

| When | Said | What happened |
|---|---|---|
| 09-12 15:53 | "start the beat up and show me the load" | The model worked out that "beat up" meant `btop` and sent `run_in_terminal: btop` to a visible foot pane. This is the right result, and it rests entirely on the model guessing the mishearing. |
| 09-12 09:16 | (debug session) | `run in terminal: journalctl -xe` 8 times, and `tmux split-window -v` once. |
| 09-13 20:09 | "Change into the GitHub folder" | `zoxide` found nothing. The model then probed paths and asked where the repos were, when the terminal already showed that folder. |
| 09-11, 09-12, 09-13 | weather, three times | Handled by web search and Chrome, never a command-line tool. That is fine, and nothing changes here. |

The `12:05:01` lines on 09-24 (`git -C ~/notes commit`, `reboot`, `date`) are
test fixtures written into the real log (#99). They are not requests.

Two typed requests, in dry run (`omarchy-voice -n say --no-confirm …`):

| Said | What the model did | Time |
|---|---|---|
| "use ffmpeg to turn the newest video in my Downloads folder into an mp3" | `ToolSearch` for `run_shell,run_in_terminal,watch_terminal`, then `run_shell "ls -t ~/Downloads/*.mp4 …"`, then offered to run it for real | 18.9 s |
| "what do I have installed that can resize a batch of images" | answered without checking: "you likely have ImageMagick's `mogrify`" | 6.8 s |

`mogrify`, `magick` and `vipsthumbnail` are all installed. The model said
"likely" because it had no way to check.

### Running is not finding: what main allows today

- **#94.** The Claude Code brain has no built-in `Bash`. Its built-ins are
  `Read` and `ToolSearch` only (`claude_backend.py:123`).
- **`run_shell`** is offered only when `hands.allow_shell` is on
  (`tools.py:1429-1431`, default `False` at `config.py:414`) and refuses
  otherwise (`tools.py:3514-3515`). It runs `bash -lc` with nothing on
  screen (`tools.py:3516`).
- **`run_in_terminal`** is **not** gated by `allow_shell`
  (`tools.py:945-965`, `3676-3697`). With the shell off, it still sends any
  single-line command to a tmux pane with `send-keys`. It has two guards. The
  first is the policy gate on the description `run in terminal: <cmd>`
  (`tools.py:1766`, `Policy.check` at `tools.py:202-212`): deny rules, then
  confirm rules. The second is the rule that the pane must be on screen
  (`tools.py:3681-3686`). `cli.py:248-253` says "`allow_shell` is the whole
  answer", and `run_in_terminal` makes that untrue. Whether this is by design
  ("the user can watch it") or a gap has never been written down, and #82
  depends on the answer.
- **#100.** Deny rules now include secret paths (`config.py:132-172`), and
  read-only tools skip confirm but not deny (`tools.py:57-60`,
  `tools.py:208-209`).
- **This machine is not on the defaults.** The Home Manager-generated
  `~/.config/omarchy-voice/config.toml` sets `allow_shell = true` and
  `deny_patterns_replace = true`. Its own deny list therefore **replaces**
  main's defaults. It has no `\bssh\b` and none of #100's secret paths. So
  the ffmpeg dry run above took the `run_shell` path. A default install
  could not have.

The consequence for scope: a finder is a read and can go with the other
read-only tools. **Running the tool it found is a shell command whatever we
call it**, because a PATH command takes arbitrary arguments. With
`allow_shell` off, `run_shell` cannot run it at all. `run_in_terminal` can,
subject only to deny/confirm patterns. With `allow_shell` on, both can.

### Three scopes

1. **A finder only.** `find_command(query)` is read-only, like `find_app`.
   It returns the name, the tldr description with aliases resolved, and a few
   of the page's example lines. It never runs anything. The model answers
   "what can resize images" truthfully and still needs an existing tool to
   act.
2. **A curated set of safe wrappers.** More fixed-argv entries in the style
   of `SYSTEM_QUERIES` (`tools.py:1326-1355`). This is safe because nothing
   the model says becomes an argument. It does not scale to "use X to do Y":
   each new request needs code, and the argument slots are exactly where the
   risk would come back in.
3. **Finder, plus running in a visible terminal.** The finder from (1), with
   `run_in_terminal` as the only way a found tool is run. The user sees the
   exact command before and while it runs, under the policy gate. Hidden
   `run_shell` stays opt-in.

**Recommendation: (1) now, with (3) as the documented execution path, and
not (2).** (1) adds no new way to run anything, so it cannot weaken #94 or
#100. (3) reuses the one executor that is already visible and already
gated. First, though, the approver has to decide whether `run_in_terminal`
should work with `allow_shell` off (open question 1). (2) remains
available for particular read-only queries, as `system_query` already does.

## Proposed outcome

- Asked "what do I have that can X", the assistant answers from what is
  **actually installed**, by name and with a one-line description, and does
  not hedge with "likely". A tool that is not installed is reported as not
  installed.
- Asked "use X to do Y", the assistant confirms X exists before building a
  command and draws on X's own examples, not on memory of some other version.
- A found command is never run by the finder. When one is run, it runs
  through an existing, gated executor, and on a default install that is one
  the user can see.
- Looking a tool up takes one read-only round trip at most and makes no
  network call.
- `find_app` behaviour does not change. "open zed" still resolves to the
  desktop entry, not to `zeditor` on PATH.

## Affected users and systems

- Every engine, since they share one `Executor`: voice (local and realtime),
  typed `say`, and the MCP server (`mcp_server.py` tool list).
- `capabilities.py`, where the index sits beside `app_index`/`find_apps`, and
  `tools.py`, for a read-only tool added to `READ_ONLY_TOOLS` and its schema.
  `persona.py` changes for steering, and `tools/verify_find.py` grows a
  command set.
- Possibly `run_in_terminal`'s gating and `cli.py`'s `shell_status`, depending
  on open question 1.
- #83 (services and MCP servers) is the sibling split out of #70 and may
  share the index shape. #101 (no sensitive-content guard on
  `read_terminal`) applies to whatever a found tool prints into a pane.

## Constraints

- **Finding must never become running.** The finder takes a query and returns
  text. It takes no arguments to pass on and executes nothing: no `--help`
  probing of arbitrary binaries, because running an unknown executable to ask
  what it does is running it.
- **No network call at lookup time.** The `tldr` CLI makes one on this
  machine, so it cannot be called per request. The page files, or a source
  built by Nix, are read directly.
- **No runtime downloads and no committed binaries** (#70's constraint). The
  current tldr cache is a runtime download. The index must not depend on one
  appearing. If tldr pages are needed, they come from nixpkgs through the
  flake, or they are optional.
- **The cached prompt prefix stays stable** (#69). A list of 1,190 commands
  does not go into the system prompt.
- **#94 and #100 hold.** No built-in `Bash` comes back, deny rules apply to
  everything, and read-only means read-only.
- **Local only.** Nothing about installed commands leaves the machine to
  build or search the index.
- **Stale is worse than slow** (#70). A newly installed command is findable
  without a daemon restart, or at worst on the next start.

## Open questions

1. **Should `run_in_terminal` work with `allow_shell` off?** Today it does,
   gated only by deny/confirm patterns and the on-screen rule. That
   contradicts `cli.py`'s "`allow_shell` is the whole answer". The choices
   are to (a) keep it and document visible execution as the intended default
   path, (b) gate it on `allow_shell` as well, or (c) keep it but always
   confirm a command that was not the user's literal words. This decides
   whether #82 can act at all on a default install.
2. **Is the finder alone enough for this issue?** That is scope (1), with
   execution left to existing tools. Or should this issue also change how a
   found tool is run (scope 3)?
3. **Where do descriptions come from when there is no tldr cache?** The
   options are nixpkgs' tldr pages through the flake, man page `NAME` lines
   read from the files (1,685 commands, no whatis database needed), or both,
   with tldr preferred. And is a command with neither in the index at all?
   Leaving it out drops helpers and also drops real tools like `nix-locate`.
4. **This machine's config.** `deny_patterns_replace = true` with a list that
   lacks `\bssh\b` and #100's secret paths. Is that intended, or should it
   extend the defaults? It is outside this repo (Home Manager), but it is the
   posture every #82 request here would run under.
