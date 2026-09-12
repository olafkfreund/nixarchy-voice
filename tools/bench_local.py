#!/usr/bin/env python3
"""Measure latency for the local pipeline: whisper -> Claude (warm) -> ElevenLabs.

This is the number the local engine lives or dies on. The realtime engine it
replaces answers in roughly 1-2s because OpenAI does speech-to-speech over one
socket; this pipeline is three separate local/cloud hops glued together, and
nothing here should be allowed to round in its favour. If the table below
shows something slower than that, say so plainly rather than averaging it away.

Three stages, timed separately, plus the two numbers that actually matter:

  transcribe   listen_local.transcribe()      whisper.cpp, this CPU
  brain        WarmBrain.ask_stream()         Claude Code, held open
  synth        elevenlabs.synth()             ElevenLabs Tarquin

  first spoken sentence   transcribe + (brain time to 1st sentence) + (synth
                           of that sentence)   <- when the user hears anything
  full reply              transcribe + brain time to the end of the reply

Text input by default — a fixed list of prompts, so the run is reproducible
and needs nobody talking. Pass --live to instead record one real utterance
per turn with `listen_local.record_utterance`, for an honest end-to-end
number that includes however long you take to stop talking.

Warm vs cold: one `WarmBrain` session is held open across all turns, and the
first turn after connecting is reported separately from the rest, so the
benefit (or lack of one) of keeping Claude Code's CLI running is visible
rather than asserted.

This calls the real Claude Code CLI and the real ElevenLabs API. Claude Code
usage draws against your subscription's plan allowance same as any other
turn; ElevenLabs synthesis is metered per character on your account. Nothing
here is free to run.

Each stage is imported lazily. The modules this measures — the local engine,
WarmBrain, the config fields that select it — may be absent or mid-change;
a missing piece prints what is missing and the benchmark runs whatever it can,
rather than a traceback.

  python3 tools/bench_local.py [--live] [--runs N] [--no-synth]
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

OPENAI_REALTIME_BASELINE = "1-2s (OpenAI realtime, speech-to-speech, one socket)"

DEFAULT_PROMPTS = [
    "What time is it?",
    "Switch to workspace three.",
    "Tell me a fun fact about Saturn in one sentence.",
    "What's on my clipboard?",
    "Say goodbye in one short sentence.",
]


class Missing(RuntimeError):
    """A module this benchmark needs is not there, or not far enough along."""


def _load():
    """Everything the benchmark touches, or a clear reason it could not.

    Returns (config_mod, listen_local, claude_backend, elevenlabs, Executor).
    Raises Missing naming exactly what was absent.
    """
    try:
        from omarchy_voice import config as cfg
    except ImportError as exc:
        raise Missing(f"omarchy_voice.config: {exc}") from exc
    try:
        from omarchy_voice import listen_local
    except ImportError as exc:
        raise Missing(f"omarchy_voice.listen_local: {exc}") from exc
    try:
        from omarchy_voice import claude_backend
    except ImportError as exc:
        raise Missing(f"omarchy_voice.claude_backend: {exc}") from exc
    if not hasattr(claude_backend, "WarmBrain"):
        raise Missing("claude_backend.WarmBrain does not exist yet")
    try:
        from omarchy_voice import elevenlabs
    except ImportError as exc:
        raise Missing(f"omarchy_voice.elevenlabs: {exc}") from exc
    try:
        from omarchy_voice.tools import Executor
    except ImportError as exc:
        raise Missing(f"omarchy_voice.tools.Executor: {exc}") from exc
    return cfg, listen_local, claude_backend, elevenlabs, Executor


async def _one_turn(brain, elevenlabs_mod, config, prompt: str,
                     synth_ok: bool) -> dict:
    """Time one already-transcribed prompt through brain (+ synth)."""
    result = {"prompt": prompt, "first_sentence": None, "first_synth": None,
              "full_reply": None, "note": ""}
    start = time.monotonic()
    first_synth_done = False
    async for sentence in brain.ask_stream(prompt):
        now = time.monotonic()
        if result["first_sentence"] is None:
            result["first_sentence"] = now - start
            if synth_ok and not first_synth_done:
                first_synth_done = True
                t0 = time.monotonic()
                try:
                    elevenlabs_mod.synth(sentence, config)
                    result["first_synth"] = time.monotonic() - t0
                except elevenlabs_mod.Unavailable as exc:
                    result["note"] = f"synth: {exc}"[:60]
    result["full_reply"] = time.monotonic() - start
    return result


def _transcribe_stage(listen_local, config, live: bool, prompt: str) -> tuple[float, str]:
    """(seconds, text-to-use). Live records+transcribes; otherwise the canned prompt, untimed."""
    if not live:
        return 0.0, prompt
    print("  listening — speak now")
    pcm = listen_local.record_utterance(device=getattr(config, "device", ""))
    if not pcm:
        return 0.0, prompt  # nothing heard; fall back to the canned line
    t0 = time.monotonic()
    try:
        text = listen_local.transcribe(pcm, config) or prompt
    except listen_local.Unavailable as exc:
        print(f"  ! transcribe unavailable: {exc}")
        return 0.0, prompt
    return time.monotonic() - t0, text


async def bench(config, cfg, listen_local, claude_backend, elevenlabs,
                 Executor, prompts: list[str], live: bool, synth_ok: bool) -> list[dict]:
    executor = Executor(config)
    # The brain the daemon actually runs, when it is there: the local engine
    # appends its own speaking rules to the system prompt, and measuring a
    # WarmBrain without them measures something nobody ships.
    try:
        from omarchy_voice import local_engine
        brain = local_engine.brain_for(config, executor)
        print("brain: local_engine.brain_for (engine persona included)\n")
    except (ImportError, AttributeError):
        brain = claude_backend.WarmBrain(config, executor)
        print("brain: claude_backend.WarmBrain (no engine persona)\n")
    rows = []
    try:
        await brain.start()
    except Exception as exc:  # noqa: BLE001 - report, do not traceback
        print(f"could not start WarmBrain: {type(exc).__name__}: {exc}")
        return rows
    try:
        for i, prompt in enumerate(prompts):
            transcribe_s, text = _transcribe_stage(listen_local, config, live, prompt)
            turn = await _one_turn(brain, elevenlabs, config, text, synth_ok)
            turn["transcribe"] = transcribe_s
            turn["cold"] = (i == 0)
            turn["first_spoken"] = (
                transcribe_s + turn["first_sentence"] + turn["first_synth"]
                if turn["first_sentence"] is not None and turn["first_synth"] is not None
                else None
            )
            rows.append(turn)
    finally:
        await brain.stop()
    return rows


def _fmt(value: float | None) -> str:
    return f"{value:6.2f}" if value is not None else "     —"


def report(rows: list[dict], synth_ok: bool) -> None:
    if not rows:
        print("nothing measured.")
        return
    print(f"\n  {'turn':4} {'warm?':>5} {'transcribe':>10} {'brain 1st':>9} "
          f"{'synth 1st':>9} {'FIRST SPOKEN':>12} {'full reply':>10}")
    print(f"  {'-'*4} {'-'*5} {'-'*10} {'-'*9} {'-'*9} {'-'*12} {'-'*10}  note")
    for i, r in enumerate(rows):
        note = r["note"]
        print(f"  {i:<4} {'cold' if r['cold'] else 'warm':>5} "
              f"{_fmt(r['transcribe']):>10} {_fmt(r['first_sentence']):>9} "
              f"{_fmt(r['first_synth']):>9} {_fmt(r['first_spoken']):>12} "
              f"{_fmt(r['full_reply']):>10}  {note}")

    warm = [r for r in rows[1:] if r["first_spoken"] is not None]
    cold = rows[0] if rows and rows[0]["first_spoken"] is not None else None
    print(f"\nBaseline being replaced: {OPENAI_REALTIME_BASELINE}")
    if cold:
        print(f"cold (first turn, includes CLI startup): "
              f"{cold['first_spoken']:.2f}s to first spoken sentence")
    if warm:
        median = statistics.median(r["first_spoken"] for r in warm)
        verdict = "within" if median <= 2.0 else "SLOWER than"
        print(f"warm (median of {len(warm)} later turns): "
              f"{median:.2f}s to first spoken sentence — {verdict} the realtime baseline")
    elif not synth_ok:
        print("ElevenLabs not configured — no first-spoken-sentence figure, "
              "only brain and transcribe stages above.")
    else:
        print("no warm turns completed — see notes above.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", action="store_true",
                         help="record a real utterance per turn instead of using canned text")
    parser.add_argument("--runs", type=int, default=len(DEFAULT_PROMPTS),
                         help="number of turns (cycles through the canned prompts)")
    parser.add_argument("--no-synth", action="store_true",
                         help="skip the ElevenLabs stage (brain latency only)")
    args = parser.parse_args()

    try:
        cfg, listen_local, claude_backend, elevenlabs, Executor = _load()
    except Missing as exc:
        print(f"cannot run: {exc}")
        print("(one of the local-engine modules is not landed yet — nothing to measure)")
        return 1

    cfg.load_env_file()
    config = cfg.load(None)

    if args.live:
        problems = listen_local.check_ready(config)
        if problems:
            print("cannot record live audio:")
            for p in problems:
                print(f"  - {p}")
            return 1

    synth_ok = not args.no_synth and elevenlabs.ready(config)
    if not synth_ok and not args.no_synth:
        print("ElevenLabs not configured (check [elevenlabs] in config.toml) — "
              "measuring transcribe + brain only, no synth stage.\n")

    prompts = [DEFAULT_PROMPTS[i % len(DEFAULT_PROMPTS)] for i in range(args.runs)]
    print(f"{len(prompts)} turns, one WarmBrain session held open across all of them.")
    print("speak = brain time to first streamed sentence. "
          "FIRST SPOKEN = transcribe + brain-1st + synth-1st.\n")

    rows = asyncio.run(bench(config, cfg, listen_local, claude_backend, elevenlabs,
                              Executor, prompts, args.live, synth_ok))
    report(rows, synth_ok)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
