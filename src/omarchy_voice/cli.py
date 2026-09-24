"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import textwrap
import subprocess
import sys
import time
from pathlib import Path

from . import (__version__, capabilities, config as cfg, hypr_events,
               listen_local, realtime as realtime_mod)
from .planner import Planner, check_ready as chat_ready
from .session import daemon_running, send_control
from . import trace as trace_mod
from .tools import attach_waker, Executor

LISTEN_ACTIONS = ("toggle", "start", "stop", "quit", "confirm", "cancel", "say")


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if sys.stdout.isatty() else text


def _tick(ok: bool) -> str:
    return "\033[32m✓\033[0m" if ok and sys.stdout.isatty() else ("✓" if ok else "✗")


# --- commands ---------------------------------------------------------------

def cmd_ask(args, config) -> int:
    """`say`, with the sentence spoken rather than typed — and no API to hear it.

    The typed path already ran against any OpenAI-compatible endpoint, so it
    worked with a local model when the account was empty or the API was down.
    What it still needed was someone to type, which is a strange requirement
    for a voice assistant and made "offline" mean "offline, and also use the
    keyboard". whisper.cpp closes that: the audio never leaves the machine, and
    with a local planner endpoint neither does anything else.
    """
    if problems := listen_local.check_ready(config):
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    print(f'{_bold("listening")} speak now — it stops when you do')
    try:
        pcm = listen_local.record_utterance(
            device=config.device, level=config.silence_level)
    except listen_local.Unavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not pcm:
        print("heard nothing.", file=sys.stderr)
        return 1
    started = time.monotonic()
    try:
        text = listen_local.transcribe(pcm, config)
    except listen_local.Unavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    seconds = len(pcm) / (listen_local.SAMPLE_RATE * 2)
    print(f'\033[2m        {seconds:.1f}s of audio, transcribed locally in '
          f'{time.monotonic() - started:.1f}s\033[0m')
    if not text:
        print("could not make out anything said.", file=sys.stderr)
        return 1
    args.text = [text]
    return cmd_say(args, config)


def choose_backend(config) -> tuple[type, str]:
    """Which brain answers `say`/`ask`, and why — the one-line answer to

    "why did this cost money here and not there".

    `claude_backend` config values: "chat" always uses Planner (OpenAI Chat
    Completions); "claude-code" prefers ClaudeBrain but falls back to Planner
    rather than hard-failing if it is not usable; "auto" (default) picks
    whichever `claude_backend.check_ready` says is usable.
    """
    if config.claude_backend == "chat":
        return Planner, "backend = chat"

    try:
        from . import claude_backend as cb
    except ImportError as exc:
        return Planner, f"claude-code backend unavailable ({exc})"

    problems = cb.check_ready(config)
    if config.claude_backend == "claude-code":
        if problems:
            return Planner, f"claude-code backend not ready: {problems[0]}"
        return cb.ClaudeBrain, "backend = claude-code"

    # "auto", or an unrecognised value -- which behaves as auto but says so,
    # rather than silently picking a backend for a typo like "claue-code".
    prefix = ""
    if config.claude_backend != "auto":
        prefix = f"backend = {config.claude_backend!r} not recognised, using auto; "
    if not problems:
        return cb.ClaudeBrain, f"{prefix}claude-code ready, using it over chat"
    # Last rung. Planner is returned either way -- there is nothing else to
    # return -- but a reason that says "using chat" when chat is equally dead
    # sends you looking at the wrong backend. Its think() then speaks the
    # failure instead of raising, which is why falling through is safe.
    if chat_problems := chat_ready(config):
        return Planner, f"{prefix}neither brain usable — chat: {chat_problems[0]}"
    return Planner, f"{prefix}claude-code not ready ({problems[0]}), using chat"


def cmd_say(args, config) -> int:
    """One command, typed instead of spoken. The whole pipeline minus the mic."""
    text = " ".join(args.text)
    executor = attach_waker(Executor(config))
    brain_cls, reason = choose_backend(config)
    planner = brain_cls(config, executor)
    print(f'{_bold("heard")}   {text}')
    print(f'\033[2m        {reason}\033[0m')
    # The daemon traces a task (local_engine.LocalSession); this path did not,
    # so the one scriptable entry point was the one that could not be measured.
    # Printed rather than logged: a scripted measurement reads stdout, and
    # there is no feedback handle here to log through.
    task = trace_mod.Trace() if config.trace_timings else None
    executor.trace = task
    spent = task.mark(trace_mod.TURN) if task else None
    turn = planner.think(text)
    if spent:
        spent.close()
    if task:
        executor.trace = None
        print(f'\033[2m        {task.finish().line()}\033[0m')
    for action in turn.actions:
        print(f'{_bold("action")}  {action}')
    if turn.error:
        print(f'{_bold("error")}   {turn.error}')
    extra = ""
    if turn.tokens:
        extra = (f", {turn.tokens.get('in', 0)} in / {turn.tokens.get('out', 0)} out")
        if "cost" in turn.tokens:
            # The CLI reports what the turn WOULD have cost through the API,
            # whether or not that is what happens. On a subscription nothing
            # is charged, so a bare dollar figure reads as a bill that does
            # not exist -- and the same number said the opposite before the
            # key was blanked, when it really was being charged. Say which.
            on_plan = (brain_cls.__name__ == "ClaudeBrain"
                       and config.claude_use_subscription)
            extra += (f", ~${turn.tokens['cost']:.4f} on your plan" if on_plan
                      else f", ${turn.tokens['cost']:.4f}")
    print(f'{_bold("reply")}   {turn.reply}   \033[2m({turn.elapsed:.1f}s{extra})\033[0m')
    if executor.pending:
        held = executor.describe(*executor.pending)
        if sys.stdin.isatty() and not args.no_confirm:
            print(f'\n{_bold("holding")} {held}')
            if input("        run it? [y/N] ").strip().lower().startswith("y"):
                outcome = executor.run_pending()
                print(f'{_bold("reply")}   {outcome.output or "Done."}')
                return 0 if outcome.ok else 1
            print("        cancelled")
        else:
            print(f'\n{_bold("waiting")} confirm to run: {held}')
        return 0
    # ClaudeBrain holds its own confirmation instead of parking it in
    # executor.pending -- there is no _tool_Bash for Executor.run_pending to
    # release, so it keeps the held call on itself and, once confirmed, asks
    # the brain to make that one call (#76). getattr rather than isinstance so any brain
    # that wants a hold can opt in through the same duck-typed seam.
    if held := getattr(planner, "pending", None):
        if sys.stdin.isatty() and not args.no_confirm:
            print(f'\n{_bold("holding")} {held}')
            if input("        run it? [y/N] ").strip().lower().startswith("y"):
                turn = planner.think(planner.confirm(), release=True)
                for action in turn.actions:
                    print(f'{_bold("action")}  {action}')
                print(f'{_bold("reply")}   {turn.reply or "Done."}')
                return 0 if not turn.error else 1
            planner.cancel()
            print("        cancelled")
        else:
            print(f'\n{_bold("waiting")} confirm to run: {held}')
        return 0
    return 0 if not turn.error else 1


REMOVED_ENGINE = ("the OpenAI realtime engine was removed in 2.0.0 (#121). "
                  'Delete engine = "openai" under [realtime], or set it to '
                  '"local". The last release with it is v1.0.0.')


def cmd_run(args, config) -> int:
    """Start the daemon on the local engine, the only one there is.

    `engine = "openai"` is refused, not quietly run as local: that would change
    the vendor and the voice without asking (#121). The refusal comes before
    the consent and policy notices, so a daemon that will not start spends
    neither. Exit 0, as the local engine's "not set up" path does, or
    systemd's restart loop makes it look like a crash. Any other value,
    including a typo, runs the local chain; `doctor` flags it.
    """
    if config.realtime_engine == "openai":
        from .feedback import Feedback
        print(REMOVED_ENGINE, file=sys.stderr)
        feedback = Feedback(config)
        feedback.state("unconfigured", REMOVED_ENGINE)
        feedback.notify("omarchy-voice", "voice has no backend: run `omarchy-voice doctor`")
        return 0
    consent_notice(config)
    policy_notice(config)
    from . import local_engine
    return local_engine.run(config)


def cmd_listen(args, config) -> int:
    command = args.action
    if args.action == "say":
        if not args.words:
            print("error: listen say needs some text", file=sys.stderr)
            return 1
        command = "say " + " ".join(args.words)
    try:
        print(send_control(command))
    except (ConnectionError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_status(args, config) -> int:
    state = {"status": "stopped", "text": "", "icon": "󰍭"}
    if cfg.STATE_FILE.exists():
        try:
            state = json.loads(cfg.STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    if not daemon_running():
        state["status"] = "stopped"
        state["icon"] = "󰍭"
        state["class"] = "stopped"
        state["text"] = ""
    if args.json:
        print(json.dumps(state))
    else:
        print(f'{state["icon"]}  {state["status"]}'
              + (f'  —  {state["text"]}' if state.get("text") else ""))
    return 0


def cmd_manifest(args, config) -> int:
    print(capabilities.manifest(refresh=args.refresh))
    if args.refresh:
        print("\n# The desktop right now\n", file=sys.stderr)
    return 0


def shell_status(config, active: str) -> list[str]:
    """What doctor says about whether this machine can run commands.

    The same on both backends (#94). With `allow_shell` off, `run_shell` is not
    offered and every other route to a command waits for a yes (#112); with it
    on, commands run without asking except where a deny or confirm rule
    matches. `active` is kept for the caller.

    A function rather than a print because a status line about what can
    execute is worth a test.
    """
    deny, confirm = len(config.deny_patterns), len(config.confirm_patterns)
    if config.allow_shell:
        return [f"  shell: on — commands run without asking, except {deny} deny rules"
                f" and {confirm} confirm rules"]
    return ["  shell: off — run_shell is not offered; any other command (terminal, "
            "launcher, compose pane, Return in a terminal) waits for your yes; "
            f"{deny} deny rules, {confirm} confirm rules"]


def voice_credit() -> list[str]:
    """The credit the shipped voice's dataset asks for, or nothing.

    The string comes from the package (OMARCHY_VOICE_ATTRIBUTION, set from the
    voice derivation's passthru) rather than being typed here, so it cannot
    drift from the Nix metadata or survive a change of voice.
    """
    credit = os.environ.get("OMARCHY_VOICE_ATTRIBUTION", "")
    return [f"  → voice: {credit}"] if credit else []


def consent_status(config) -> list[str]:
    """What doctor says about the two capabilities that start off (#17).

    A helper, not four prints, for the same reason `shell_status` is one: the
    desktop line is conditional on whether ai-mirror is even installed, and a
    status line about what can move the real mouse is worth a test.
    """
    if config.desktop_control:
        from .claude_backend import ai_mirror_path
        found = ai_mirror_path()
        where = f"ai-mirror at {found}" if found else "but ai-mirror is not installed"
        desktop = f"  desktop control: enabled, {where}"
    else:
        desktop = ("  desktop control: disabled (set [hands] desktop_control = true, "
                   "or programs.omarchy-voice.desktopControl)")
    log = ("  notification log: "
           + ("enabled, bodies are recorded" if config.allow_notifications
              else "disabled (records notification bodies when on)"))
    return [desktop, log]


def consent_notice(config) -> None:
    """Say once, to a user who never chose, that the notification log is off.

    Once rather than per start: a line that prints every morning is a line
    nobody reads. Someone who set the key made the choice already and hears
    nothing.
    """
    if config.allow_notifications_explicit:
        return
    marker = cfg.STATE_DIR / "notifications-off-noticed"
    if marker.exists():
        return
    text = ("omarchy-voice no longer records notification bodies by default. "
            "Set [hands] allow_notifications = true to turn the log back on.")
    cfg.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with cfg.LOG_FILE.open("a") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text}\n")
    if config.notify and shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "OMA", "-u", "low", "--", "omarchy-voice", text],
                       capture_output=True)
    # After the log, and whether or not notify-send exists: the marker records
    # that it was said, not that it was seen.
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()


def policy_notice(config) -> None:
    """Log removed or replaced built-in rules on every start (#109).

    Every start, unlike `consent_notice`: a removed deny rule is current state,
    not news.
    """
    if not config.policy_notes:
        return
    cfg.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with cfg.LOG_FILE.open("a") as fh:
        for ok, text in config.policy_notes:
            line = f"{'✓' if ok else '✗'} {text}"
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {line}\n")
            print(f"omarchy-voice: {line}", file=sys.stderr)


def policy_status(config) -> list[str]:
    """What doctor says about removed or replaced built-in rules (#109)."""
    return [f"  {_tick(ok)} {text}" for ok, text in config.policy_notes]


def gate_hint(active: str) -> list[str]:
    """Where doctor points at `verify-gate`, on the backend it exists for.

    The claude-code backend's policy gate rests on how the installed `claude`
    treats hooks, which a Claude Code upgrade can change without anything
    here noticing. Doctor names the check next to the version it depends on,
    and never runs it: it spends plan allowance and takes a minute, and
    doctor is neither.
    """
    if active != "claude-code":
        return []
    return ["    after a Claude Code upgrade, run: omarchy-voice verify-gate"]


def cmd_doctor(args, config) -> int:
    print(_bold(f"omarchy-voice {__version__}\n"))

    print(_bold("openai"))
    key = bool(os.environ.get(config.api_key_env))
    print(f"  {_tick(key)} {config.api_key_env}"
          + ("" if key else f"  (put it in {cfg.ENV_FILE})"))
    print(f"  → planner model {config.planner_model} (`omarchy-voice say`)")
    print(f"  → realtime model {config.realtime_model}, voice {config.realtime_voice}")
    if cfg.ENV_FILE.exists():
        mode = cfg.ENV_FILE.stat().st_mode & 0o777
        print(f"  {_tick(mode & 0o077 == 0)} {cfg.ENV_FILE} mode {mode:o}")
    else:
        print(f"  {_tick(False)} {cfg.ENV_FILE} missing")
    if cfg.SAFETY_ID_FILE.exists():
        print(f"  {_tick(True)} per-install safety identifier at {cfg.SAFETY_ID_FILE}")

    print(_bold("\nbrain"))
    brain_cls, reason = choose_backend(config)
    active = "claude-code" if brain_cls.__name__ == "ClaudeBrain" else "chat"
    print(f"  → {active} — {reason}")
    try:
        from . import claude_backend as cb
        cli = cb.cli_path(config)
    except ImportError:
        cb, cli = None, ""
    # Both rungs of the ladder, not just the one in use. "why is it answering
    # on chat" used to be unanswerable here: the only clue was the one-line
    # reason, and a machine that was simply offline looked misconfigured.
    rungs = [("claude-code", cb.check_ready(config) if cb else ["module not importable"]),
             ("chat", chat_ready(config))]
    for name, problems in rungs:
        if problems:
            print(f"  {_tick(False)} {name} unusable: {problems[0]}")
        else:
            print(f"  {_tick(True)} {name} usable")
    if cli:
        version = cb.cli_version(cli)
        print(f"  {_tick(True)} claude CLI at {cli}" + (f" ({version})" if version else ""))
        for line in gate_hint(active):
            print(line)
    else:
        print(f"  {_tick(False)} no claude CLI found "
              "(OMARCHY_VOICE_CLAUDE_CLI unset, and `claude` not on PATH)")
    creds = Path.home() / ".claude" / ".credentials.json"
    print(f"  {_tick(creds.exists())} {creds}")
    if active == "claude-code":
        # Which account pays, stated from what is actually true rather than
        # from what this backend is for. The CLI prefers an API key over the
        # claude.ai login whenever one is set, so on a machine that exports
        # ANTHROPIC_API_KEY -- this one does -- the backend silently billed
        # per token until it started blanking the key for the subprocess. A
        # doctor that says "draws on the Claude plan" regardless is worse than
        # one that says nothing: it is the line someone checks before trusting
        # it, and it was wrong by $0.1255 a sentence.
        anthropic_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        if not config.claude_use_subscription:
            print(f"  {_tick(False)} claude_use_subscription = false — turns bill "
                  "the Anthropic API,")
            print("    not your plan. Unset it to use the claude.ai login.")
        elif anthropic_key:
            print(f"  {_tick(True)} ANTHROPIC_API_KEY is set but blanked for the "
                  "Claude Code subprocess,")
            print("    so turns use your claude.ai login and the plan rather than "
                  "the key.")
        else:
            print("    usage draws on the Claude plan, not OpenAI API credit.")
        print(f"  → would fall back to: chat (Planner, model {config.planner_model})")

    print(_bold("\nengine"))
    from . import local_engine
    local = config.realtime_engine != "openai"
    if local:
        voice = local_engine.voice_chain(config)
        print("  → local — the whole chain runs from parts on this machine:")
        # Claude Code, not `active`: the daemon holds a warm session open and
        # does not fall back to chat the way `say` does.
        print(f"    whisper.cpp  ▸  Claude Code ({config.claude_model})  ▸  "
              f"{voice or 'NO VOICE'}")
        for line in voice_credit():
            print(line)
        print("    Nothing is sent to OpenAI. Audio in never leaves the machine;")
        print("    the thinking goes to Claude, and the voice to ElevenLabs if it")
        print("    is configured. She is held quiet while the microphone is open.")
        engine_problems = local_engine.check_ready(config)
    else:
        print(f"  → openai — speech to speech over a websocket, "
              f"{config.realtime_model} in {config.realtime_voice}")
        print("    Room audio is streamed to OpenAI while listening is on, and")
        print('    the reply comes back as audio. Set engine = "local" under')
        print("    [realtime] for the whisper ▸ Claude ▸ ElevenLabs chain.")
        engine_problems = realtime_mod.check_ready(config)
    if engine_problems:
        for problem in engine_problems:
            for n, line in enumerate(textwrap.wrap(problem, 70)):
                print(f"  {_tick(False)} {line}" if n == 0 else f"    {line}")
    else:
        print(f"  {_tick(True)} every part of the chain is present")

    print(_bold("\nears"))
    problems = realtime_mod.check_ready(config)
    if problems:
        for problem in problems:
            print(f"  {_tick(False)} {problem}")
    else:
        print(f"  {_tick(True)} websockets, API key, and PipeWire tools all present")
    print(f"  → OpenAI Realtime (speech to speech), "
          f"{config.realtime_turn_detection}, toggle-only")
    print("  ! while listening is on, room audio is streamed to OpenAI.")
    print("    It starts off, and only the voice toggle key turns it on. Toggling")
    print("    off kills the recorder, so nothing is captured while muted.")
    if config.silence_gate:
        print(f"  {_tick(True)} silence gate on — room tone stops being uploaded "
              f"{config.silence_hold_seconds:g}s after")
        print("    the last thing said, with a short pre-roll when speech resumes.")
    else:
        print(f"  {_tick(False)} silence gate off — every frame is uploaded, "
              f"including an empty room")
    if config.idle_stop_seconds > 0:
        print(f"  {_tick(True)} listening stops itself after "
              f"{config.idle_stop_seconds // 60} min with nothing said")
    else:
        print(f"  {_tick(False)} idle_stop_seconds = 0 — an open microphone "
              f"stays open until toggled")
    if config.realtime_transcribe_model:
        print(f"  ! transcribe_model = {config.realtime_transcribe_model} — a second "
              f"model runs over")
        print("    all input audio, billed on top of the realtime session, for the log only.")
    local_problems = listen_local.check_ready(config)
    if config.wake_word:
        if local_problems:
            print(f"  {_tick(False)} wake word {config.wake_word!r} configured but "
                  f"cannot run:")
            for problem in local_problems:
                print(f"    {problem}")
        else:
            print(f"  {_tick(True)} wake word {config.wake_word!r} — heard locally by "
                  f"whisper.cpp while")
            print("    listening is off. No audio leaves the machine until it fires.")
    else:
        print("  → no wake word; listening starts only from the key or the widget")
    if local_problems:
        print(f"  {_tick(False)} `omarchy-voice ask` unavailable: {local_problems[0]}")
    else:
        print(f"  {_tick(True)} `omarchy-voice ask` can transcribe on this machine")
    source = config.device or listen_local.default_source()
    print(f"  default input: {source or '(none)'}")
    print(f"  output:        {listen_local.default_sink() or '(none)'}")
    if config.barge_in:
        risk = listen_local.echo_risk(config)
        print(f"  {_tick(not risk)} barge_in is on — you can interrupt her mid-sentence")
        if risk:
            for line in textwrap.wrap(risk, 72):
                print(f"    {line}")
    else:
        print("  → the microphone is held shut while she speaks, so her own voice")
        print("    cannot come back in as yours. Set barge_in = true (headphones,")
        print("    or PipeWire echo-cancel) to interrupt her mid-sentence.")

    print(_bold("\nhands"))
    for tool in ("hyprctl", "omarchy", "wtype", "notify-send", "uwsm-app"):
        print(f"  {_tick(bool(shutil.which(tool)))} {tool}")
    # Reported because a listener that has quietly died degrades to the poll
    # and stays correct -- which is the failure mode to want, and also the one
    # nobody would otherwise notice.
    events = hypr_events.socket_path()
    if events:
        print(f"  {_tick(True)} compositor events ({events.parent.name[:16]}…)")
        print("    → waits wake on openwindow rather than polling every 150ms")
    else:
        print(f"  {_tick(False)} compositor events: no socket2 found")
        print("    → waits still work, on a 30ms-then-150ms poll")
    for line in shell_status(config, active):
        print(line)
    for line in policy_status(config):
        print(line)
    for line in consent_status(config):
        print(line)
    if config.unknown_keys:
        print(f"  {_tick(False)} unknown config keys (ignored): {', '.join(config.unknown_keys)}")
    for key in config.retired_keys:
        # A setting that used to work is a different problem from a typo, and
        # deserves the reason rather than being lumped in with misspellings.
        print(f"  {_tick(False)} `{key}` no longer does anything — {cfg.RETIRED_KEYS[key]}")

    print(_bold("\nmanifest"))
    manifest = capabilities.manifest()
    versions = capabilities.system_versions()
    unreadable = capabilities.unreadable_sources()
    print(f"  {_tick(not unreadable)} {len(manifest)} chars, "
          f"built from Omarchy {versions['omarchy']}")
    for problem in unreadable:
        for line in textwrap.wrap(problem, 68):
            print(f"      {line}")
    # verify_essentials returns an empty list both when everything resolves
    # and when it could not read the routes to check. Without this the second
    # case prints the same green tick as the first, which is the one reading
    # you must not be able to get from a machine that answered nothing.
    routes_readable = not any("omarchy commands" in p for p in unreadable)
    broken = capabilities.verify_essentials()
    if not routes_readable:
        print(f"  {_tick(False)} could not check the {len(capabilities.ESSENTIALS)} "
              "common actions — no routes to check against")
    elif broken:
        print(f"  {_tick(False)} {len(broken)} common action(s) no longer resolve to an omarchy route:")
        for item in broken:
            print(f"      {item}")
    else:
        print(f"  {_tick(True)} all {len(capabilities.ESSENTIALS)} common actions resolve")
    # Same shape as the routes above: no stub to parse means the check did not
    # run, which is not the same answer as "they all exist".
    stub_readable = not any("hl.meta.lua" in p for p in unreadable)
    broken_hypr = capabilities.verify_hypr_essentials()
    if not stub_readable:
        print(f"  {_tick(False)} could not check the "
              f"{len(capabilities.HYPR_ESSENTIALS)} dispatcher examples — "
              "no Hyprland API stub to check against")
    elif broken_hypr:
        print(f"  {_tick(False)} {len(broken_hypr)} dispatcher example(s) no longer exist:")
        for item in broken_hypr:
            print(f"      {item}")
    else:
        print(f"  {_tick(True)} all {len(capabilities.HYPR_ESSENTIALS)} dispatcher examples exist")
    print(f"  cache: {cfg.CACHE_DIR}")

    print(_bold("\ndaemon"))
    running = daemon_running()
    print(f"  {_tick(running)} {'running' if running else 'not running'}"
          f"  ({cfg.SOCKET_PATH})")
    # The exit code is about the engine that will actually start. A machine
    # running the local chain with no OPENAI_API_KEY is not unhealthy, and
    # doctor exiting 1 over it sends someone hunting for a key they removed
    # on purpose.
    hard = [p for p in engine_problems
            if "audio input" not in p and "loopback" not in p]
    return 1 if hard else 0


def cmd_mcp(args, config) -> int:
    from . import mcp_server
    return mcp_server.run(config)


def cmd_verify_gate(args, config) -> int:
    """Run after a Claude Code upgrade. See verify_gate for why."""
    from . import verify_gate
    return verify_gate.run(config)


def cmd_log(args, config) -> int:
    if not cfg.LOG_FILE.exists():
        print("no log yet")
        return 0
    if args.follow:
        subprocess.run(["tail", "-f", str(cfg.LOG_FILE)])
    else:
        lines = cfg.LOG_FILE.read_text().splitlines()
        print("\n".join(lines[-args.lines:]))
    return 0


# --- parser -----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omarchy-voice",
        description="Drive Omarchy by voice, with OpenAI Realtime as the router.",
    )
    parser.add_argument("--version", action="version", version=f"omarchy-voice {__version__}")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="decide, but narrate actions instead of running them")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--config", type=Path, help="alternate config.toml")
    parser.add_argument("--model", help="planner model for `say` (default: gpt-4.1)")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("say", help="run one command as if it had been spoken")
    p.add_argument("text", nargs="+")
    p.add_argument("--no-confirm", action="store_true",
                   help="never prompt for held actions; just report them")
    p.set_defaults(func=cmd_say)

    p = sub.add_parser(
        "ask", help="speak one command — heard locally, no API for the audio")
    p.description = (
        "Records one sentence, transcribes it with whisper.cpp on this machine, "
        "then runs it exactly as `say` would. The audio never leaves the "
        "machine; with a local planner endpoint (see [openai] base_url) nothing "
        "does. Unlike `run`, there is no websocket and no realtime session."
    )
    p.add_argument("--no-confirm", action="store_true",
                   help="do not prompt for held actions")
    p.set_defaults(func=cmd_ask, text=[])

    p = sub.add_parser("run", help="start the listening daemon")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("listen", help="control a running daemon")
    p.add_argument("action", choices=list(LISTEN_ACTIONS))
    p.add_argument("words", nargs="*",
                   help="text for `listen say`")
    p.set_defaults(func=cmd_listen)

    p = sub.add_parser("status", help="what the daemon is doing (for the bar)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("doctor", help="check every moving part")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser(
        "verify-gate",
        help="prove the policy gate holds on this Claude Code version "
             "(4 model turns on your plan)")
    p.set_defaults(func=cmd_verify_gate)

    p = sub.add_parser("manifest", help="print what the model knows about this machine")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(func=cmd_manifest)

    p = sub.add_parser(
        "mcp",
        help="serve the desktop tools over MCP, on stdio",
        description=(
            "Offer the same tools the voice session drives to an MCP client. "
            "Actions go through the same policy gate and the same confirm hold, "
            "because it is the same Executor.\n\n"
            "  claude --mcp-config '{\"mcpServers\":{\"omarchy\":"
            "{\"command\":\"omarchy-voice\",\"args\":[\"mcp\"]}}}'"
        ),
    )
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("log", help="what it heard and did")
    p.add_argument("-n", "--lines", type=int, default=40)
    p.add_argument("-f", "--follow", action="store_true")
    p.set_defaults(func=cmd_log)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg.ensure_dirs()
    env_warnings = cfg.load_env_file()
    if args.command == "doctor":
        cfg.warn_env_permissions(env_warnings)
    config = cfg.load(
        args.config,
        dry_run=args.dry_run or None,
        verbose=args.verbose or None,
        planner_model=getattr(args, "model", None),
    )
    return args.func(args, config)


if __name__ == "__main__":
    sys.exit(main())
