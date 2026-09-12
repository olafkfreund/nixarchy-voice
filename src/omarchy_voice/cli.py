"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import textwrap
import subprocess
import sys
from pathlib import Path

from . import __version__, capabilities, config as cfg, realtime as realtime_mod
from .planner import Planner
from .session import daemon_running, send_control
from .tools import Executor

LISTEN_ACTIONS = ("toggle", "start", "stop", "quit", "confirm", "cancel", "say")


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if sys.stdout.isatty() else text


def _tick(ok: bool) -> str:
    return "\033[32m✓\033[0m" if ok and sys.stdout.isatty() else ("✓" if ok else "✗")


# --- commands ---------------------------------------------------------------

def cmd_say(args, config) -> int:
    """One command, typed instead of spoken. The whole pipeline minus the mic."""
    text = " ".join(args.text)
    executor = Executor(config)
    planner = Planner(config, executor)
    print(f'{_bold("heard")}   {text}')
    turn = planner.think(text)
    for action in turn.actions:
        print(f'{_bold("action")}  {action}')
    if turn.error:
        print(f'{_bold("error")}   {turn.error}')
    extra = ""
    if turn.tokens:
        extra = (f", {turn.tokens.get('in', 0)} in / {turn.tokens.get('out', 0)} out")
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
    return 0 if not turn.error else 1


def cmd_run(args, config) -> int:
    return realtime_mod.run(config)


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
    source = config.device or realtime_mod.default_source()
    print(f"  default input: {source or '(none)'}")
    print(f"  output:        {realtime_mod.default_sink() or '(none)'}")
    if config.barge_in:
        risk = realtime_mod.echo_risk(config)
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
    print(f"  shell tool: {'enabled' if config.allow_shell else 'disabled'}"
          f", {len(config.deny_patterns)} deny rules"
          f", {len(config.confirm_patterns)} confirm rules")
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
    hard = [p for p in problems if "audio input" not in p and "loopback" not in p]
    return 1 if hard else 0


def cmd_mcp(args, config) -> int:
    from . import mcp_server
    return mcp_server.run(config)


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
