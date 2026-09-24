# nixarchy-voice

Operate Omarchy by talking to it. By default no OpenAI *service* is involved
at all: a wake word or the toggle key is heard locally, whisper.cpp
transcribes it on this CPU, your Claude subscription answers, and ElevenLabs
(falling back to Piper) speaks the reply. `[realtime] engine = "openai"`
switches the daemon back to OpenAI's Realtime speech-to-speech socket instead
— see [Speech without OpenAI](#speech-without-openai).

> A fork of [**omarchy-voice**](https://github.com/wombatoperator/omarchy-voice)
> by **Britain Eriksen**, ported to NixOS. The daemon, the capability manifest,
> the policy gate and the persona are his work; this fork makes it run on
> [nixarchy](https://github.com/olafkfreund/nixarchy) and drops the Arch install
> path. See [Credit and what this fork changed](#credit-and-what-this-fork-changed).

```
you   "put my email on workspace three, then go there"
      → hypr_query(clients)                       looks for the window
      → hypr_dispatch window.move {"workspace": "3", "window": "address:0x55d4..."}
      → hypr_dispatch focus {"workspace": "3"}
it    "Moved HEY to workspace 3."
```

Omarchy already ships **Voxtype** for dictation — speech becomes *text*. This is
the other half: speech becomes *actions*. Voxtype keeps F9; this is on whatever
key you bind, since the upstream default of `SUPER + SHIFT + V` is taken on some
machines.

Installed as a flake input and a Home Manager module, with the bar widget linked
in as an Omarchy shell plugin.

## Why an LLM instead of a phrase grammar

A grammar makes you learn its vocabulary. The interesting part of this add-on
is **what the model is told**. On first run it reads your live system and
builds a capability manifest from it:

| Source | What it contributes |
|---|---|
| Hyprland's own `hl.meta.lua` type stub | every Hyprland dispatcher |
| Omarchy's `default/hypr/bindings/*.lua` | real, version-correct call syntax |
| `omarchy commands --json` | the whole Omarchy CLI, with arguments and summaries |
| `hyprctl -j` + your `.desktop` files | your monitors, workspaces, windows, and installed apps |

Hyprland 0.56 moved to a Lua dispatch API: `hyprctl dispatch workspace 1` is
dead, and a model working from memory writes it anyway. Reading the API off
the machine means the assistant is correct for *your* Hyprland and *your*
Omarchy, and stays correct after an update.

Run `omarchy-voice manifest` to read exactly what it knows.

## How it works

`[realtime] engine = "local"` (the default):

```
wake word / toggle ─▶ whisper.cpp ─▶ Claude (warm) ─▶ ElevenLabs ─▶ speakers
     (pw-record)      this CPU        subscription      / Piper
                                          │  function calls
                                          ▼
                                    policy gate ──▶ denied / held for confirmation
                                          │
                                          ▼
                            hyprctl · omarchy · wtype · uwsm-app
```

`[realtime] engine = "openai"`:

```
microphone ══▶ websocket ══▶ gpt-realtime ══▶ audio ══▶ speakers
 (pw-record)   while live         │             (pw-cat)
                                  ▼  function calls
                            policy gate ──▶ denied / held for confirmation
                                  │
                                  ▼
                    hyprctl · omarchy · wtype · uwsm-app
```

The OpenAI engine has no local transcription step and no wake word: you
talk, it stops and answers, and **while listening is on, room audio streams
continuously to OpenAI.** Muting kills the `pw-record` process rather than
capturing audio and discarding it. The local engine trades that for a wake
word and a transcript instead of your raw voice — see
[Speech without OpenAI](#speech-without-openai) for what that costs and what
it buys back.

`omarchy-voice say "..."` is the typed equivalent, on either engine: same
tools, same policy gate.

## Install

Nixarchy only — this fork drops the Arch install path. Add the flake as an
input and turn it on in your Home Manager configuration:

```nix
{
  inputs.nixarchy-voice.url = "github:olafkfreund/nixarchy-voice";

  # ... in your home configuration:
  imports = [ inputs.nixarchy-voice.homeModules.default ];

  programs.omarchy-voice = {
    enable = true;
    environmentFile = config.age.secrets.openai-api-key.path;
    settings = {
      realtime.voice = "marin";
      hands.allow_shell = false;
    };
  };
}
```

`environmentFile` holds `OPENAI_API_KEY=sk-...`. Keep it out of the Nix store —
a store path is world-readable and ends up in every backup of the machine.
Point it at an agenix/sops secret, or write `~/.config/omarchy-voice/env` by
hand with mode 600. A key exported in your shell does not reach a systemd user
service.

The module installs the package, links the bar widget into
`~/.config/omarchy/plugins`, runs the daemon as a user service, and writes the
toggle binding to `~/.config/hypr/voice-binds.lua`. It still does **not** write
`~/.config/hypr/bindings.lua` — Hyprland reads exactly one of those and it is
yours — so load the fragment from it with one line:

```lua
pcall(require, "hypr.voice-binds")
```

`pcall` rather than a bare `require`, because your bindings.lua outlives any
generation that stops providing the file. This is the same shape nixarchy's own
`gog-binds` and `meet-binds` fragments use.

You are told to add that line at activation time, and only while it is missing
— a build warning cannot see your bindings.lua, so it would either nag on every
rebuild forever or say nothing and let the key quietly do nothing. Set
`bindsFile = false` to go back to pasting the bind by hand.

Check the key is free first — `hyprctl binds -j` is the only honest answer, and
on the machine this was developed on all three of `SUPER + V`, `SUPER + CTRL +
V` and the upstream `SUPER + SHIFT + V` were already taken.

Putting the widget on the bar is still `omarchy bar put
olafkfreund.voice-indicator --section right`, because that writes to your
mutable `shell.json`, which the module does not own.

Check your work:

```bash
omarchy-voice doctor
```

### The desktop, for a coding agent

`omarchy-voice mcp` serves the same tools over MCP on stdio, so Claude Code or
Codex can drive the desktop directly:

```bash
claude --mcp-config '{"mcpServers":{"omarchy":{"command":"omarchy-voice","args":["mcp"]}}}'
```

```bash
codex mcp add omarchy -- omarchy-voice mcp
```

Nothing is re-implemented. The schemas are the ones the model already sees and
the executor is the one the realtime session calls, so an action taken through
an agent passes the same policy gate, the same confirmation hold and the same
transcript as one taken by voice. Told to restart the machine with "the user
has already approved it", Claude Code was held, and would not go through a
terminal to do it either.

A held action is released with `confirm_last`, carrying the user's own words —
checked against the same `confirm_words` a spoken confirmation is checked
against — or dropped with `cancel_last`. Both are offered over MCP only; by
voice the equivalents take what was heard. An agent cannot confirm its own
hold: a confirmation arriving in the seconds after it, before anyone could have
been asked, is refused and the action stays held. Denied actions are not
confirmable by any route.

Two resources are offered rather than pushed into the prompt —
`omarchy://manifest` for what this machine can do, `omarchy://state` for what
is open right now. An agent manages its own context and asks when it wants
them, which is the opposite of the voice session's problem.

### A different model, or none of OpenAI's

`omarchy-voice say` will talk to anything that speaks OpenAI's
`/v1/chat/completions` — Ollama, LM Studio, vLLM, OpenRouter — because what it
needs back is tool calls in that shape, not OpenAI specifically:

```toml
[openai]
base_url = "http://localhost:11434/v1"
planner_model = "qwen3:14b"
```

A localhost endpoint needs no API key and none is sent.

This does **not** move `run` when `[realtime] engine = "openai"` — that engine
speaks OpenAI's websocket protocol, which nothing else implements, so
speech-to-speech stays on the API however this is set. What moves is the
typed path — `say` and `--dry-run` — which is also the one you want working
when the API is down or the account is out of credit. Set `[realtime] engine
= "local"` (the default) instead if you want `run` itself off OpenAI; see
[Speech without OpenAI](#speech-without-openai).

Claude works through Anthropic's OpenAI-compatible endpoint:

```toml
[openai]
base_url = "https://api.anthropic.com/v1"
planner_model = "claude-sonnet-4-5"
api_key_env = "ANTHROPIC_API_KEY"
```

That bills the Anthropic API — a Claude subscription does not reach this
endpoint — and it formats replies for a screen unless the persona tells it
otherwise, which is wrong for something read aloud.

### Or your Claude subscription instead of an API key

The other way to reach Claude costs nothing metered. Point `say`/`ask` at
the Claude Agent SDK (`claude_agent_sdk`), and it drives the **Claude Code**
CLI you are already logged into over OAuth — so a turn spends part of your
Claude Pro/Max plan's usage, the same as running `claude` in a terminal,
rather than Anthropic API credit:

```toml
[openai]
claude_backend = "claude-code"     # or "auto" to fall back to the HTTP planner
claude_model = "claude-sonnet-5"   # full id, never an alias — see below
```

This needs `claude` on PATH already — the package does **not** install it.
That's deliberate, not an oversight: Claude Code updates itself against a
fast-moving API, and a copy pinned through Nix would go stale the moment
upstream shipped a fix, staying stale until someone bumped this flake by
hand. Every other tool this daemon shells out to (`wtype`, `grim`,
`whisper-cpp`, ...) is declarative because none of them need to change
underneath you week to week; this one dependency stays imperative on
purpose. `omarchy-voice doctor` reports the path, version, and login state
it finds. If `claude` isn't on PATH — a user install, an odd `$PATH` in the
systemd unit — point at it explicitly with `claude_cli` in `config.toml` or
the `OMARCHY_VOICE_CLAUDE_CLI` environment variable, which takes priority.

On `[realtime] engine = "openai"` this does not touch `run` either, for the
same reason the OpenAI-compatible endpoint above doesn't: that engine is
OpenAI speech-to-speech over a websocket, and nothing else speaks that
protocol. On the default `engine = "local"`, `run` *is* this exact
`WarmBrain` — held open across the whole session rather than spawned per
turn, which is what makes a conversation viable at all; see
[Speech without OpenAI](#speech-without-openai).

Two things worth knowing before turning it on:

- **It gives the model our tools and two of Claude Code's own: `Read` and
  `ToolSearch`.** No `Bash`, `Write`, `Edit` or `WebFetch`, so `allow_shell`
  means what it says. None of your own MCP servers, plugins, skills, settings
  or `~/.claude/CLAUDE.md` is loaded, so a deny rule in
  `~/.claude/settings.json` does not apply here either; a managed
  `/etc/claude-code/CLAUDE.md` still is. Every call, including the few
  resource readers Claude Code adds by itself, goes through the same
  deny/confirm/dry-run policy in a `PreToolUse` hook, so it covers the calls
  Claude Code approves on its own, not only the ones it would have asked
  about, and is written to `omarchy-voice log`, allowed or refused. But that
  policy is regexes over a tool description, not a sandbox, so this is a
  wider attack surface than the HTTP planner ever had.
- **After upgrading Claude Code, run `omarchy-voice verify-gate`.** The gate
  depends on how the installed CLI treats hooks, which an upgrade can change
  without any test here noticing. It runs four real cases against your CLI —
  about a minute, four model turns on your plan — and exits 0 only if the gate
  still holds. `doctor` shows the command beside the CLI version; it never runs
  it.
- **Usage draws against your plan's allowance**, not a separate budget. A
  heavy session can hit a plan rate limit the same way a long Claude Code
  session on the CLI would.
- Use the full model id (`claude-sonnet-5`), not an alias — an alias can
  silently resolve to an older model once it's the CLI resolving it instead
  of the API.

This approach is from [**backtalk**](https://github.com/jaredrhod/backtalk)
by **Jared Rhodenizer**, AGPL-3.0.

Choose the model for tool calling rather than size. The planner asks for
function calls, and a model that is weak at them returns the JSON as prose
instead of calling anything: `qwen2.5-coder:14b` did that here, `qwen3:14b`
did not. Expect tens of seconds against ~10k tokens of manifest rather than
the ~2 that `gpt-4.1` takes.

### Hearing without the API

`omarchy-voice ask` records one sentence, transcribes it with whisper.cpp on
this machine, and runs it exactly as `say` would:

```bash
omarchy-voice ask
listening speak now — it stops when you do
```

The audio never leaves the machine. Point `base_url` at Ollama as above and
nothing does — which is the case the typed path was always for, except that it
previously required you to type, so "offline" also meant "and use the
keyboard".

The model is packaged; no download runs on first use. `base.en` by default,
overridable like the Piper voice:

```nix
programs.omarchy-voice.package =
  inputs.nixarchy-voice.packages.${pkgs.stdenv.hostPlatform.system}.omarchy-voice.override {
    whisperModel = (pkgs.callPackage "${inputs.nixarchy-voice}/nix/whisper-model.nix" { })
      ."tiny.en";
  };
```

### The wake word

Off by default. With one set, the daemon listens locally while listening is
**off**, and starts a session when it hears it:

```toml
[ears]
wake_word = "oma"
```

This is the feature that makes the cost work above mostly moot: the expensive
thing was leaving listening switched on, and a wake word means never needing
to. Nothing reaches OpenAI until the word is heard — the audio goes to
whisper.cpp on this CPU, and only once somebody actually speaks, so a quiet
room costs one blocked read and no CPU at all.

It does mean a microphone is open locally whenever listening is not on, which
is why it is opt-in rather than a default.

Short names get misheard. `omarchy-voice log` records every snippet the wake
listener considered and rejected:

```
wake    ignored 'Ohma, are you there?'
wake    heard 'Oma, close the browser'
```

Add the spelling that keeps coming back as a second word — `wake_word = "oma
ohma"` — rather than arguing with the transcriber. Matching is on whole words,
so "aroma" and anyone called Omar do not wake her.

### Her local voice

Spoken status lines go through Piper. The package carries one voice —
`en_GB-jenny_dioco-medium`, British and conversational — because Piper refuses
to start without one. Turn it on in `config.toml`:

```toml
[mouth]
speak = true
```

Two others are packaged (`en_GB-cori-high`, `en_US-lessac-high`), and any
voice from [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices)
works:

```nix
programs.omarchy-voice.package =
  inputs.nixarchy-voice.packages.${pkgs.stdenv.hostPlatform.system}.omarchy-voice.override {
    piperVoice = (pkgs.callPackage "${inputs.nixarchy-voice}/nix/piper-voice.nix" { })
      ."en_GB-cori-high";
  };
```

On `[realtime] engine = "openai"` this is **not** the voice she answers in — a
realtime session gets its audio from OpenAI as audio, that is `[realtime]
voice`, and it never touches Piper. Piper there is the local fallback: status
lines, and anything spoken when no realtime session is up.

On the default `engine = "local"`, Piper *is* the fallback voice for replies
too — the primary one is ElevenLabs, next.

### Speech without OpenAI

The default engine is a pipeline built from parts this repo already has, so
that no OpenAI *service* is used to hold a conversation at all:

```
wake word / toggle → whisper.cpp → Claude (warm, subscription) → ElevenLabs
```

Measured on this machine with `tools/bench_local.py`: whisper transcription
**1.50s**, ElevenLabs synthesis **0.28s**, and Claude **6.4-9.2s** on a cold,
per-turn CLI spawn — which is why the brain here is `WarmBrain`, one Claude
Code session held open and streamed sentence-by-sentence instead of one
process per turn. Even warm it measured a 1.5-7.6s median to the first spoken
sentence in the same run, against OpenAI realtime's roughly 1-2s. **Say that
plainly: the local engine is not as fast as the thing it replaces**, some
turns land in range and some do not, and the gap is entirely the extra CLI
round trip Claude Code costs per turn. Run the benchmark yourself before
relying on it — `tools/bench_local.py` reports the same split, on your
hardware and your network, warm and cold.

"Whisper" here is [OpenAI's Whisper](https://github.com/openai/whisper) *model*
running locally through [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
— open weights, no OpenAI account, no network call, no per-use cost. It is
not OpenAI's Whisper *API*, and nothing about it reaches OpenAI; the name is
just confusing that way.

`[realtime] engine = "local" | "openai"`, local by default:

```toml
[realtime]
engine = "local"
```

What is lost leaving speech-to-speech: the model gets a transcript, not your
voice, so tone and emphasis never arrive — sarcasm and emphasis-by-volume both
flatten into the same words. Barge-in becomes stopping local playback rather
than the server cancelling generation mid-token, so an interruption lands a
beat later. Turn-taking is our own silence gate (`[ears] silence_hold_seconds`)
standing in for OpenAI's `semantic_vad`, which listens for a completed thought
rather than just a pause.

What is gained: one voice everywhere instead of the OpenAI-only Realtime
voices, nothing metered by OpenAI, and it keeps answering when there is no
network at all — point `[openai] base_url` at Ollama and use Piper instead of
ElevenLabs, and the whole chain runs offline. It also draws on your Claude
subscription's usage instead of a metered API key, same trade as
[the typed path above](#or-your-claude-subscription-instead-of-an-api-key).

The OpenAI engine is not going anywhere — `[realtime] engine = "openai"`
keeps the original speech-to-speech session exactly as documented in
[How it works](#how-it-works) above, for when semantic turn-taking and actual
tone matter more than staying off OpenAI's meter.

### Without a flake

```bash
nix run github:olafkfreund/nixarchy-voice -- doctor
nix shell github:olafkfreund/nixarchy-voice   # then: omarchy-voice run
```

### Requirements

- Nixarchy (Omarchy 4.x on NixOS, Hyprland 0.56+)
- `claude` on PATH and logged in — the default `[realtime] engine = "local"`
  answers on your Claude subscription, not an API key. `OPENAI_API_KEY` is
  only needed if you set `[realtime] engine = "openai"`.
- Optional: an ElevenLabs account and voice id for the local engine's reply
  voice — see [Speech without OpenAI](#speech-without-openai). Without one it
  falls back to Piper, which needs nothing.
- A microphone PipeWire can see — `doctor` will tell you if the default
  input is a monitor loopback
- **Nothing, for clicking.** This used to ask for
  `programs.ydotool.enable = true;` and a re-login for the `ydotool` group.
  Clicking now goes through Wayland's own `zwlr_virtual_pointer_v1`, so there
  is no root daemon and no `/dev/uinput` — **you can remove
  `programs.ydotool.enable` if you added it for this**. Everything the daemon
  shells out to — `wtype`, `grim`, `tesseract`, `wl-clipboard`, `pw-record`,
  `tmux`, and the input helper — is wrapped onto PATH by the package.

  The helper comes from [ai-mirror](https://github.com/olafkfreund/ai-mirror),
  as a flake input. Only the 478-line C binary: no MCP server, no Python. It
  is 53 MiB against ydotool's 104 MiB, so the package got smaller.

  **SUPER + SHIFT + ESCAPE stops it.** That is ai-mirror's revoke, and voice
  answers it too, so one keypress ends every synthetic input on the machine
  rather than one program's. Voice does not need ai-mirror's control to be
  *granted* — it never asks for it — and if ai-mirror is not installed at all
  there is simply nothing to revoke.

  The reason for the change is not speed. A turn abandoned between a key press
  and its release used to leave the key down, with nothing in this program able
  to let go. The helper's life is the turn's life, so an abandoned chord is
  released by the abandonment.

You get:

| Key | Does |
|---|---|
| whatever you bound | turn listening on, and off again |

That is the only way in. There is no hold-to-talk and no always-on mode: the
daemon starts muted, and while it is muted no recorder is running, so there is
nothing to leak. `SUPER + V` (Universal paste) and `SUPER + CTRL + V`
(clipboard manager) are Omarchy's and are left alone.

To remove it, set `enable = false` and rebuild. The binding block in
`bindings.lua` and the bar entry in `shell.json` are yours to take back out —
the module never wrote them.

## Use

```bash
omarchy-voice run                     # the daemon — or the user service
omarchy-voice say "..."               # one command, typed instead of spoken
omarchy-voice --dry-run say "..."     # decide, but narrate instead of acting
omarchy-voice listen toggle           # what the keybinding calls
omarchy-voice listen confirm          # local confirm of a held action
omarchy-voice listen cancel
omarchy-voice status --json           # for the bar
omarchy-voice log -f
```

`--dry-run` still runs read-only queries (`hypr_query`) so the planner can
see live windows; it only narrates the actions that would change the desktop.

### As an Omarchy command

`bin/omarchy` finds its subcommands by listing the directory its own file sits
in — not PATH, and with no plugin directory or environment override. So the
routes have to be built into the Omarchy package. Point
`programs.nixarchy.package` at a copy that carries them:

```nix
programs.nixarchy.package =
  inputs.nixarchy-voice.lib.${pkgs.stdenv.hostPlatform.system}.withVoiceRoutes
    (pkgs.extend inputs.nixarchy.overlays.default).omarchy;
```

That is the same seam nixarchy's own `nixarchy-*` commands use: the routes are
installed into `share/omarchy/bin` and linked into the `bin/` symlink farm over
it. Nothing in nixarchy has to know voice control exists. Then:

```bash
omarchy voice                       # what it is doing
omarchy voice toggle                # same as the key
omarchy voice say "..."             # one request, typed
omarchy voice doctor
```

They are thin wrappers over the same program, with the `# omarchy:summary=`
metadata Omarchy's dispatcher reads, so they appear in `omarchy commands`
alongside everything else. Declining costs you nothing but the spelling —
`omarchy-voice ...` works either way.

### Things to say

> "switch to workspace four" · "close this window" · "put this on the left
> monitor" · "make it full screen" · "float this and centre it"
>
> "move everything off this workspace onto three" · "close the terminal that's
> running the build, not this one" · "open my email and put it beside the
> browser" · "what's on workspace two?" · "switch to a dark theme"
>
> "open a new tab" · "search this page for pipewire" · "save the file"

> "how much disk space have I got left?" · "what's making the fan spin?" ·
> "am I still on wifi?" · "what time is it?"
>
> "scroll down and read me the rest" · "copy that link and tell me what it
> says" · "does it say anything about pipewire?" · "wait for the build to
> finish, then tell me if it passed"

### Searching

Oma has a search engine, and the results land on your screen rather than in a
token stream:

```
you   "how much is a bitcoin worth right now"
      → web_search({ query = "current Bitcoin price in USD" })
it    "It shows Bitcoin at about 79,148 dollars and 79 cents."
```

The query goes in the **URL** and the results open as their own window. Nothing
is typed, which matters more than it sounds: the web panes on an Omarchy desktop
are `chrome --app=<url>` windows with no tab bar and no address bar, so `CTRL+T`
and `CTRL+L` are no-ops and there is nowhere for a typed query to go. A session
log of the assistant discovering that, the hard way, is in `HANDOFF.md`.

`scope` picks the page: `web` (Google, whose answer panel often answers outright),
`news`, `images`, `videos`, or `duckduckgo` for a plain list of links. `images`
and `videos` are not read back — you asked to see them, so it says so and leaves
them on screen.

`open_page(url)` is the same mechanism for one specific address. Prefer both over
`omarchy launch browser <url>`, which opens a tab inside a window that already
exists: nothing new appears in `hyprctl`, so it cannot be waited for, read, or
verified. Oma is told this, and the tool refuses it with the right call named.

### Terminals, and being told when something is done

A terminal used to be a *picture* — grim the window, run tesseract, hope. Now it
goes through tmux, which Omarchy already ships:

```
you   "run the tests and tell me when they're done"
      → run_in_terminal({ command = "python3 -m unittest discover -s tests" })
it    "That's running; I'll say when it finishes."
      ⋮ (you go and do something else, on another workspace)
it    "The tests finished in 19 seconds and all 327 passed. Want me to carry on?"
```

That last line is the only thing this daemon ever says without being asked.
`capture-pane` gives exact text from a pane on **any** workspace — or none, or
with the display asleep — and `pane_current_command` dropping back to your shell
is the "it's done" signal, no heuristics. `send-keys` takes the key by name, so
none of the keysym trouble applies.

`run_in_terminal` only runs in a pane you can actually see: tmux must have a
client *and* a terminal window must be on a workspace the compositor is
currently drawing. An open microphone should not be able to run things in a
window you have no view of. Reading and watching have no such limit — they are
safer than `read_screen`, which ships a picture of your screen to OpenAI.

Announcements wait for a reply in flight to finish, never land closer together
than 8 seconds, and become a desktop notification instead of speech when
listening is off.

### Going after a goal

The interesting requests are not one action. "Get me to that pull request and
tell me what changed" is a loop — do a thing, look at what happened, decide
what is next — and the tools are shaped so that loop can actually close:

| Tool | The wall it removes |
|---|---|
| `scroll` | The screen shows one screenful. What is below the fold does not exist to `read_screen` or `click_text` until you scroll to it. |
| `wait_for` | Pages load, applications start. Reading a moment too early shows you the previous screen — and it gets reported as the new one. |
| `clipboard` | OCR guesses at pixels. A URL, an error, a code has to be right to the character; the clipboard is exact. |
| `system_query` | "How much space is left", "am I on wifi", "why is the fan loud" are questions about the machine, not about a window. Read-only, no shell. |
| `remember` | When listening is toggled off the conversation is gone. This is the only memory a goal spanning two sittings has. |
| `web_search` / `open_page` | Anything you do not know or cannot see. Results open as a real window — visible to you, and readable, scrollable and clickable by her. |
| `read_terminal` / `run_in_terminal` / `watch_terminal` | A terminal as exact text rather than OCR, on any workspace or none — and an interruption when a long job ends. |
| `read_screen(query=…)` | A screenful of OCR is a couple of thousand tokens. Ask for the line you need and pay for the line you need. |

`max_turns` (default 12) is how many tool rounds one spoken instruction gets
before Oma stops and waits to be asked again.

### Asking for a task, not an application

Name a subject rather than a program and it builds a workspace for it:

> "what's going on in the news today" · "set me up to watch the match and
> follow the chat" · "I want to work on the budget"

```
you   "what's going on in the news today"
      → compose_windows({ layout = "columns", workspace = "next",
                          panes = [ AP News, Reuters, BBC News ] })
it    "I opened AP News and Reuters side by side in a new workspace."
```

It picks the sources, opens them on the first empty workspace, and lays them
out — waiting for each window to actually map before placing the next, which
is the part that cannot be done from the outside. `columns` puts equal panes
side by side, `main-and-side` keeps one large with the rest stacked beside it,
`grid` is a 2x2. Whatever did not come up is named in the reply rather than
reported as open.

## Speed

Every turn re-sends the whole system prompt, and OpenAI counts those tokens
against a tokens-per-minute limit whether or not they were served from cache.
So the size of the prompt *is* how many things you can say in a minute:

```bash
omarchy-voice manifest | wc -c     # the biggest part of it
```

At 40,000 TPM and roughly 10,200 tokens a turn that is about four turns a
minute; at the 800,000 a full tier-3 realtime bucket gives you, it stops
mattering. `omarchy-voice log` now records what the server says your ceiling
actually is on every turn (`limits  tokens: 797790/800000 left`) — worth
checking, because the two are not always the same. Past that the API starts refusing responses; the daemon waits
the interval the server names and asks again rather than going quiet, but it
cannot make the budget bigger. If Oma feels like it is pausing between
sentences, that is what is happening — check your tier at
[platform.openai.com/account/rate-limits](https://platform.openai.com/account/rate-limits).

`tools/bench_realtime.py` measures time-to-first-action across realtime models
on this machine. `tools/bench_realtime.py` is for `engine = "openai"`;
`tools/bench_local.py` measures the local engine's transcribe/brain/synth
split, warm vs. cold — see [Speech without OpenAI](#speech-without-openai)
for the numbers it produced here.

## What it costs

The prompt is the part you can count ahead of time. The audio is the part that
actually shows up on the bill, and until recently none of it was measured:
`rate_limits.updated` was logged, which says how much budget is *left*, not what
was spent. Every response now logs what it cost, cancelled and rate-limited
turns included, because those were paid for too:

```
usage   in 11024 (audio 612, text 10412, cached 9984) out 284 (audio 240, text 44) | session audio 8213 in / 3960 out
```

Watch `cached`. The snapshot is appended as a conversation item rather than
rewritten into `instructions` precisely so the ~10k-token prefix stays cached;
a session where that number sits near zero has a cache being invalidated every
turn, which is a bug rather than a price.

Three things keep the audio bill down, all on by default:

**Silence is not uploaded.** Listening used to stream the room continuously —
an empty room billed at the same audio rate as speech. Room tone now stops
being sent `silence_hold_seconds` after the last thing said, and a 400 ms
pre-roll is flushed when speech resumes so the opening syllable survives. The
hold is load-bearing: server-side turn detection ends a turn by hearing the
pause after it, so a gate that shut the instant you stopped talking would hold
back the very silence the turn end is inferred from, and no reply would ever
come. Do not set it near zero.

**An open microphone closes itself.** `idle_stop_seconds`, ten minutes by
default, switches listening off as if the toggle had been pressed. The
websocket stays up, so coming back is the same keypress with no reconnect and
no `session.update` — the cached prefix survives the pause. Listening is a mode
you enter and forget, and forgetting it used to stream the room until you came
back.

**Old turns are deleted.** Everything still in the conversation is re-sent as
input on every later turn, so a session's per-turn cost used to climb with how
long it had been up: the four-turns-a-minute figure above is the best case,
measured at the start of a session rather than an hour into one.
`history_items` caps it, dropping whole old turns and never orphaning a tool
result from the call it answers.

Transcription of your own audio (`realtime.transcribe_model`) is off by
default. It is a second model run over every second of input audio, on top of
the realtime model already listening to it, and the only thing that consumed
the result was two lines in the session log. Turn it on when you need to tell a
misheard command from a bad decision — which is exactly when it earns the
money.

Reach costs tokens. The tools above add about 2,000 to every turn — roughly one
turn a minute — which is the price of Oma being able to finish a multi-step job
instead of stopping at the first thing she cannot see. It is a better trade than
it looks: the session that could not search burned **twelve** tool rounds failing
to, which is two minutes of budget for no answer. The same question now costs one
round and eight seconds. `run_shell` is no longer
sent at all unless `allow_shell = true`, since a tool that will only ever be
refused costs its schema every turn and a whole round trip when reached for.

## Speakers, and her hearing herself

If Oma's voice comes out of speakers rather than headphones, it goes into the
room and back into an open microphone. The server's turn detection treats that
as you talking: it cancels the reply she is halfway through and transcribes her
own words as a command. From a real session log, on a machine whose mic and
line out were the same interface —

```
reply  'OH-mah, OH-mah, OH-mah.'
error  response cancelled: turn_detected
heard  '어마'                    ← her own name, back through the microphone
reply  'Yes, I'm here.'
```

— and later her own sentence returned as two user turns, which she apologised
for not catching. A fragment that transcribes as an instruction is not merely
noise: one arrived as `'Бела.'` and pressed CTRL+R.

**So the microphone is held shut while she is speaking**, plus 350 ms for the
room to go quiet. That is the default and it needs no configuration. The only
thing it costs is interrupting her mid-sentence — which on speakers did not
work anyway, because she was the one doing the interrupting.

To get that back, use headphones or PipeWire's echo canceller
(`share/echo-cancel.conf`), then:

```toml
[ears]
barge_in = true
```

`omarchy-voice doctor` reports your input and output devices and warns if you
have turned barge-in on with both ends on the same box.

## Full desktop control with ai-mirror

Install [ai-mirror](https://github.com/olafkfreund/ai-mirror)
(`programs.ai-mirror.enable = true` from its flake) and the Claude brain can be
given its tools as a second MCP server: real mouse and keyboard, screenshots,
and the accessibility tree of every app. `click_text` and `send_shortcut` stay
first choice; ai-mirror is for everything they cannot reach.

**It is off until you say otherwise.** Having ai-mirror installed is not a
decision to hand over the mouse, so the link is made only when you ask for it:

```toml
[hands]
desktop_control = true
```

or, from a configuration:

```nix
programs.omarchy-voice.desktopControl = true;
```

With it off, `ai-mirror` on PATH changes nothing and the tools are never
offered. `omarchy-voice doctor` says which state you are in, and whether
ai-mirror is actually installed.

Every ai-mirror call passes the same deny/confirm gate as every other call,
and ai-mirror itself asks you to confirm before any agent takes control. While
she drives, the bar shows AGENT CONTROL, and SUPER + SHIFT + ESCAPE takes it
back.

## Voices and credits

The spoken voice is [piper](https://github.com/rhasspy/piper) with a voice from
[rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices). Each voice
ships its own `MODEL_CARD` into the store beside the model, because the weights
and the corpus they were trained on are not under the same terms.

| Voice | Dataset | Credit |
|---|---|---|
| `en_GB-jenny_dioco-medium` (default) | [Jenny TTS (Dioco)](https://github.com/dioco-group/jenny-tts-dataset) | **Jenny (Dioco)** — shown in `omarchy-voice doctor` and in the bar widget's tooltip, because the dataset asks to be credited wherever the voice speaks |
| `en_GB-cori-high` | [LibriVox](https://librivox.org), public domain | none required |
| `en_US-lessac-high` | [Blizzard 2013 / Lessac](https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/) | the weights are published MIT, but the dataset's own licence is a **per-licensee research agreement** that excludes commercial use. Read its `MODEL_CARD` before choosing this voice. |

The credit string comes from the voice derivation's `passthru.attribution`
through `OMARCHY_VOICE_ATTRIBUTION`, so changing the voice changes the credit
and neither can drift from the other.

## Safety

An open microphone is an untrusted input channel. The model's decisions are
not trusted blindly:

- **Denied outright**: `rm -rf`, `dd`, `mkfs`, `sudo`, `pkexec`, `ssh`,
  `passwd`, piping curl into a shell, `git push`, and reads or writes of
  well-known secret files (`/etc/shadow`, `~/.ssh`, `~/.gnupg`, `.env`,
  `/run/agenix`, `/run/secrets`, cloud and CLI credentials).
- **Held for confirmation**: shutdown, reboot, suspend, package installs,
  `omarchy update`, config resets, closing every window.
- **Never held**: lookups (`omarchy_help`, `find_app`, `find_command`,
  `read_screen`, `read_terminal`, `hypr_query`, `system_query`, `screenshot`,
  `list_terminals`, and Claude Code's `Read`). `find_command` reads PATH
  listings, man pages and tldr pages and runs nothing. Asking *about* a reboot is
  not a reboot. Deny rules still apply to them, so to stop a read you
  write a deny rule, not a confirm rule.
- **Blocked as process execution**: the `exec_cmd` / `exec_raw` dispatchers,
  and `launch_app` command lines. Apps launch by desktop id; URLs must be
  `http(s)`. `allow_shell = true` is the only way around that.
- **Not written as Lua**: `hypr_dispatch` names a dispatcher and takes its
  arguments as values; it does not accept a Lua expression. Until 0.3.1 it did,
  and that was a hole — Hyprland 0.56 evaluates the argument position, so
  `hl.dsp.focus((function() hl.exec_cmd("...") return { workspace = "1" } end)())`
  ran a shell command on an install with `allow_shell = false`, where the
  shell tool is not even offered to the model. The deny list still saw the text
  and still caught `rm -rf`, but a blocklist was never meant to be the only
  barrier. Fixed in 0.3.1 ([#22](https://github.com/olafkfreund/nixarchy-voice/issues/22)).
  **On `allow_shell = true` installs raw Lua is still accepted, by design** —
  there the shell tool is already offered, so it grants nothing new.
- **Off by default**: timing records (`[hands] trace_timings`), which write one
  line per finished task to `session.log` — the total, the number of model
  round trips a tool result cost, and seconds per phase. Phase names and
  durations only: not what was on screen, not the window, not the tool's
  arguments, and by construction rather than by filtering. It is still a record
  of when you were using the machine, so it is opt-in.
- **Off by default**: the shell tool; **desktop control** through ai-mirror
  (`[hands] desktop_control`); and the **notification log**
  (`[hands] allow_notifications`), which when on records notification bodies —
  message previews included — to
  `~/.local/state/omarchy-voice/notifications.jsonl`. Turning the log on is
  still the cheaper answer to "what was that notification" than `read_screen`,
  which sends a picture of the whole desktop; it is just not a choice to make
  on someone's behalf. If you never set the key, the first start after this
  change says once, in the log and as a notification, that it is off.
- **Leaves the machine**: `read_screen` sends a picture of the screen, and
  `clipboard` read sends whatever you last copied. Both go to OpenAI along with
  the audio. `read_screen` and `click_text` refuse outright when the session is
  locked, so a lock screen is never captured or clicked through.

Confirmation is not the model's to grant:

1. A gated tool and `confirm_last` in the **same** response is rejected.
2. `confirm_last` only runs after a **new user turn** (speech or `listen say`).
3. The phrase is matched as a whole utterance, so "don't confirm" does not
   confirm.
4. Clicking the bar widget while it says "waiting", or
   `omarchy-voice listen confirm`, releases the hold **locally** — that path
   never asks the model.

On the local engine, the engine hears "confirm" itself. The model is denied
`confirm_last` outright, and a confirm or cancel said while something is held
never reaches the model. Her spoken prompt says "the word on the screen" and
never says the word itself; the notification names it. A spoken confirm is
refused out loud, with the way forward, when it starts within
`[ears] spoken_confirm_guard_seconds` (default 1.0) of her last playback, or
when she said that phrase herself since the turn began. A spoken cancel is
refused only by the first check. The guard is measured from the moment the
player returns, so a `tts_command` that backgrounds its own playback makes it
start too soon. The second check still holds in that case. With `barge_in`
on, a spoken confirm is always refused: use the key. A spoken cancel still
works.

The lists live in `~/.config/omarchy-voice/config.toml`. Extra
`confirm_patterns` / `deny_patterns` / `sensitive_patterns` are *added* to
the built-in lists.

To drop one built-in rule and keep the rest, name it in the matching
`*_remove` list:

```toml
[hands]
deny_patterns_remove = ["ssh"]   # ssh by voice; every other deny rule still applies
```

`*_remove` takes the rule names below, never a regex: a name stays the same
when the pattern under it is rewritten, so rules added upstream later still
reach you. `omarchy-voice doctor` and the start-up log say which rules were
removed. An unknown name removes nothing and is reported. A pattern you add
yourself stays in the list even when it is also a removed default's pattern.
A `*_remove` that names every built-in rule of its list is not applied, and
`doctor` says so. `ssh` covers the command only: reading `~/.ssh` is still
denied by `secret-ssh-dir`. A denial names the rule that blocked it, as in
``blocked by deny rule `ssh` (/\bssh\b/)``.

`confirm_patterns_replace = true` (and `deny_` / `sensitive_`) throws the
defaults away and uses only your list, which you then maintain by hand. With
replace on, `*_remove` is ignored, and `doctor` names every built-in rule whose
exact pattern is not in your list.

The control socket lives under `$XDG_RUNTIME_DIR` (mode 700, socket 600). The
daemon refuses to start if that directory is not owner-only.

### Rule names

Deny (29):

| Name | Pattern |
|---|---|
| `rm-rf` | `\brm\s+-[a-zA-Z]*[rf]` |
| `mkfs` | `\bmkfs\b` |
| `dd` | `\bdd\s+if=` |
| `shred-wipefs` | `\b(shred\|wipefs)\b` |
| `write-block-device` | `>\s*/dev/[sn][dv]` |
| `passwd` | `\bpasswd\b` |
| `sudo` | `\bsudo\b` |
| `pkexec` | `\bpkexec\b` |
| `cryptsetup` | `\bcryptsetup\b` |
| `curl-pipe-shell` | `\bcurl\b.*\\|\s*(ba)?sh` |
| `git-push` | `\bgit\s+push\b` |
| `ssh` | `\bssh\b` |
| `nix-collect-garbage` | `\bnix-collect-garbage\b` |
| `nix-store-gc` | `\bnix\s+store\s+(delete\|gc)\b` |
| `nix-store-delete` | `\bnix-store\s+--delete\b` |
| `nix-profile-wipe-history` | `\bnix\s+profile\s+wipe-history\b` |
| `nix-env-delete-generations` | `\bnix-env\s+--delete-generations\b` |
| `secret-shadow` | `/etc/g?shadow\b` |
| `secret-ssh-dir` | `/\.ssh(/\|\b)` |
| `secret-gnupg` | `/\.gnupg(/\|\b)` |
| `secret-agenix-sops` | `/run/(agenix\|secrets)(\.d)?(/\|\b)` |
| `secret-dotenv` | `(^\|[\s/"'=])[\w-]*\.env(\.local\|\.production\|\.development)?(?=$\|[\s"';\|&)])` |
| `secret-ssh-key` | `\bid_(rsa\|ecdsa\|ed25519\|dsa)\b(?!\.pub)` |
| `secret-login-stores` | `/\.(netrc\|git-credentials\|pgpass)\b` |
| `secret-aws` | `/\.aws/credentials\b` |
| `secret-gh-token` | `/\.config/gh/hosts\.yml\b` |
| `secret-claude-login` | `/\.claude/\.credentials\.json\b` |
| `secret-pass-store` | `/\.password-store(/\|\b)` |
| `secret-keyrings` | `/\.local/share/keyrings(/\|\b)` |

Confirm (17):

| Name | Pattern |
|---|---|
| `shutdown` | `\bshutdown\b` |
| `reboot` | `\breboot\b` |
| `poweroff` | `\bpoweroff\b` |
| `suspend` | `\bsuspend\b` |
| `hibernate` | `\bhibernat` |
| `omarchy-update` | `\bomarchy\s+update\b` |
| `omarchy-drive` | `\bomarchy\s+drive\b` |
| `omarchy-pkg` | `\bomarchy\s+pkg\b` |
| `omarchy-install` | `\bomarchy\s+install\b` |
| `omarchy-refresh` | `\bomarchy\s+refresh\b` |
| `omarchy-reinstall` | `\bomarchy\s+reinstall\b` |
| `hyprland-exit` | `\bhl\.dsp\.exit\b` |
| `close-all` | `\bclose[-_ ]?all\b` |
| `nixos-rebuild` | `\bnixos-rebuild\b` |
| `home-manager-switch` | `\bhome-manager\s+switch\b` |
| `nixarchy-apply` | `\bnixarchy-apply\b` |
| `nix-flake-update` | `\bnix\s+flake\s+update\b` |

Sensitive windows (5):

| Name | Pattern |
|---|---|
| `password-manager` | `1password\|bitwarden\|keepass\|keepassxc\|proton.?pass\|gnome-keyring\|seahorse` |
| `credential-prompt` | `polkit\|pinentry\|gcr-prompter\|kwalletd\|hyprlock\|omarchy-lock\|swaylock` |
| `private-browsing` | `private browsing\|incognito\|inprivate\|private window\|navigation priv` |
| `credential-text` | `password\|passcode\|2fa\|one-time\|\botp\b` |
| `banking` | `\bbank\b\|banque\|revolut\|paypal\|stripe dashboard\|credit card\|carte bancaire` |

## How it is put together

Two pieces, on purpose:

| Piece | Where it lives | How it ships |
|---|---|---|
| Bar widget | `plugin/olafkfreund.voice-indicator/` | A normal Omarchy shell plugin (`kinds: ["bar-widget"]`). On nixarchy the Home Manager module registers it through `programs.nixarchy.plugins`, which validates and reconciles it; on Omarchy without nixarchy it is linked into `~/.config/omarchy/plugins/`. Putting it on the bar is still `omarchy bar put olafkfreund.voice-indicator --section right`, because that writes to your mutable `shell.json`. |
| Daemon | `src/`, `share/` | `nix/package.nix` wraps it with its runtime tools on PATH; `nix/hm-module.nix` wires the user service and the plugins. |

The daemon deliberately stays a separate package rather than moving into the
Omarchy tree: that tree is a read-only store path here, and upstream it is
owned by the omarchy package and overwritten on update. The `omarchy voice ...`
routes get in through `lib.withVoiceRoutes` instead.

`nix flake check` runs the whole suite in the sandbox and is the gate before
you push.

## Bar widget

Shows idle / listening / thinking / acting / waiting-for-confirm. Click
toggles listening; click while waiting **confirms** the held action.

The Home Manager module links it in for you (`barWidget = true`, the default),
so all that is left is placing it:

```bash
omarchy bar put olafkfreund.voice-indicator --section right
```

That writes to your mutable `shell.json`, which the module does not own — and
there is no `omarchy bar remove`, so taking it off again is an edit to that
file by hand.

**Upgrading:** the plugin ids gained a prefix — `voice.indicator` is now
`olafkfreund.voice-indicator`, and `voice.orb` is `olafkfreund.voice-orb`. If
the widget was on your bar, a one-time `post-boot.d` hook moves it at your next
login (through `omarchy plugin`, never by editing `shell.json`), and a plugin
you had switched **off** stays off. A script of your own that runs `omarchy bar
put voice.indicator` has to change; nothing else does. The hook records itself
in `~/.local/state/omarchy-voice/ids-migrated` only once every step has
succeeded, so a failure retries at the next login.

There is no `omarchy bar remove`; take the entry out of `shell.json` by hand.

## Configuration

`~/.config/omarchy-voice/config.toml` — see `share/config.example.toml`.

## Layout

```
bin/omarchy-voice              launcher
src/omarchy_voice/
  capabilities.py              builds the manifest from the live system
  persona.py                   shared instructions for Realtime and `say`
  planner.py                   OpenAI Chat Completions loop for `say`
  tools.py                     the tools, and the policy gate
  realtime.py                  speech-to-speech engine and confirm gate
  session.py                   control socket (toggle / confirm / cancel)
  feedback.py                  notifications, bar state, TTS
  cli.py                       say / run / listen / status / doctor / manifest
  mcp_server.py                the same tools, over MCP, for a coding agent
plugin/olafkfreund.voice-indicator/
                               Omarchy shell bar widget
share/                         example config, PipeWire echo-cancel note
nix/                           package, Home Manager module, piper voices
```

## Credit and what this fork changed

**[omarchy-voice](https://github.com/wombatoperator/omarchy-voice) is by
[Britain Eriksen](https://github.com/wombatoperator)**, MIT licensed. The
architecture is his and it is the reason this port was four small fixes rather
than a rewrite: the capability manifest reads Hyprland's own LuaLS type stub
and harvests real dispatcher calls out of Omarchy's shipped keybindings, so the
desktop API is never hardcoded and a version bump does not silently break it.
The policy gate, the confirm hold, the persona and the tool set are his work
too. This fork changed almost none of that.

What it did change:

### Made it run on NixOS

Four failures, and every one of them was silent — a thinner manifest or an
unverified keypress, with nothing in any log:

* **Keysym validation was off.** `keys.py` resolves keysyms through
  libxkbcommon by `ctypes`, and on a miss passes every name through
  unverified rather than refusing it. `ctypes.util.find_library` finds nothing
  unless the library is in the closure, so `"Enter"` reached Hyprland instead
  of being refused with "did you mean Return?". The wrapper puts it on
  `LD_LIBRARY_PATH`.
* **`/usr/share/omarchy` and Hyprland's stub** are read from the environment
  now. Omarchy already exports `OMARCHY_PATH`, so the dispatcher tree and its
  version-correct examples come back.
* **Desktop entries** are found through `XDG_DATA_DIRS` rather than two
  duplicated hardcoded lists. The manifest had been telling the model this
  machine had no software installed on it.
* **A dangling `.desktop` symlink** took the whole manifest down with it.
  Every entry is a symlink here and a collected generation leaves the link
  behind.

### Fixed three things that had never worked anywhere

Not NixOS problems — code that could not have run on any distribution:

* **`barge_in` did nothing.** `ECHO_TAIL_SECONDS`, `Speaker.is_playing(tail)`
  and a `_held_frames` counter all existed, with six tests; the one line in the
  mic loop that used them was missing, so every frame went to the server
  including her own voice through the speakers. She interrupted herself on
  every reply and never finished a sentence. The existing tests all passed
  against the broken loop because they only exercise `Speaker` in isolation.
* **Interruptions were reported as failures.** `response.done` treated every
  status but `completed` as a dead turn, and the server returns `cancelled`
  on every normal interruption — so each one produced a notification and a
  spoken line, which on speakers fed straight back into the microphone.
* **Piper could not start.** `piper --output-raw` was called with no `-m
  MODEL`, which piper refuses outright, and the branch also required `aplay`,
  so the failure was invisible behind the espeak fallback. The package now
  carries a voice, reads the sample rate from it rather than assuming 22050,
  and plays through `pw-cat`.

### Added

* **A flake**: package, Home Manager module, overlay, dev shell, and
  `nix flake check` running the whole suite hermetically.
* **`lib.withVoiceRoutes`** so `omarchy voice ...` works. `bin/omarchy` finds
  subcommands by listing its own directory, which is read-only here, so the
  routes are built into the Omarchy package the same way nixarchy's own
  commands are.
* **A pluggable planner** — `base_url` points `say` at anything speaking
  OpenAI's `/v1/chat/completions`: Ollama, Claude, OpenRouter, LM Studio. A
  localhost endpoint needs no API key and none is sent.
* **An MCP server** — `omarchy-voice mcp` offers the same tools to Claude Code
  or Codex, through the same executor and therefore the same policy gate.
* **Coding agents in the manifest**, so she reaches for `claude -p` or
  `codex exec` through a terminal rather than hunting the application list.
* **NixOS-specific deny rules.** Collecting garbage removes every generation
  you could roll back to, needs no sudo, and matched nothing in the gate.

Arch support was removed rather than kept in parallel: `install.sh`,
`uninstall.sh`, the hand-rolled unit and the upstream service installer are all
gone, and the install path is a flake input.

MIT, as upstream. Copyright remains with the omarchy-voice contributors; the
NixOS port is under the same licence.
