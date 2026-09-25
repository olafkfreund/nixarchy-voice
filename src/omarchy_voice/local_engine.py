"""The local engine: heard here, thought on your plan, spoken in her voice.

It is the parts this repo already had, wired into the loop the OpenAI
realtime engine (removed in 2.0.0, #121) used to be:

    toggle / wake word ─▶ pw-record ─▶ whisper.cpp ─▶ Claude (warm) ─▶ sentence
        session.py        listen_local  listen_local   claude_backend     │
                                                                         ▼
                                          pw-cat ◀── ElevenLabs / piper (feedback)

What was lost against the removed realtime engine (#121) is real: it heard
tone, not words, and could be interrupted mid-sentence because the microphone
never closed. This one gets a transcript — a flat sentence with the sarcasm removed — and
interrupts crudely, because the microphone is shut while she talks.

What is gained is that no audio leaves the machine on the way in, the thinking
bills the Claude subscription rather than metered audio, the voice is the same
one `say` uses, and the whole chain still answers with the network down (piper
under ElevenLabs, whisper on this CPU).

A turn is strictly sequential — record, transcribe, think, speak — so the
microphone gate is the loop's shape rather than a flag someone has to
remember to check. Sentences are spoken as the brain produces them: waiting
for the whole reply would hand back the second that streaming was meant to
save.
"""

from __future__ import annotations

import asyncio
import math
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import elevenlabs, feedback as feedback_mod, listen_local, notifications, router
from . import trace as trace_mod
from .config import Config
from .feedback import Feedback
from .session import ControlServer, _matches, _normalize
from .tools import attach_waker, Executor

# Speakers and a room both lag: the tail is PipeWire's buffer plus however long
# the reflection takes to die. Without it the last syllable of a reply comes
# back through the mic a moment after playback "ended" and reopens the gate on
# her own voice.
ECHO_TAIL_SECONDS = 0.35

# How often the background watcher asks tmux whether a watched command has
# finished. Two seconds is well under the time it takes anyone to notice, and
# the poll is one `tmux list-panes`, which costs nothing.
WATCH_POLL_SECONDS = 2.0


def watch_headline(job: dict) -> str:
    """One sentence for a finished watch: the log line, and the notification."""
    if job["vanished"]:
        return f"The pane running {job['label']} was closed."
    if job["timed_out"]:
        return f"{job['label']} is still going after a long time."
    return f"{job['label']} finished in {job['seconds']:.0f} seconds."


def watch_message(job: dict) -> str:
    """What the model is handed to announce a finished watch, on either engine."""
    tail = (job["tail"] or "").strip()
    return (
        f"# A watched command finished\n\n{watch_headline(job)} It ran in tmux pane "
        f"{job['target']}.\n\nThe last of what it printed:\n\n{tail}\n\n"
        "Tell the user now, unprompted and in one short sentence: what "
        "finished, and whether it looks like it worked, from the output "
        "above rather than from hope. Then ask if they want you to carry "
        "on. They did not just speak to you — do not answer as though "
        "they had."
    )


# session: anything with `async run() -> int`.
def _run_until_done(session: Any) -> int:
    """asyncio.run, but a stuck worker thread cannot wedge the exit.

    Tool calls run through `asyncio.to_thread`, which uses the loop's default
    executor, and `asyncio.run` waits for that executor to drain before it
    returns. A tool still blocked on a subprocess therefore kept the process
    alive after the session had ended and its control socket was gone: `ps`
    showed a healthy daemon, `omarchy-voice status` said no daemon is running,
    and systemd — seeing a process that had not exited — never restarted it.

    Owning the executor lets us abandon it instead of waiting on it. The threads
    are daemon threads doing bounded subprocess work; the process is exiting
    either way.
    """
    executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="omarchy-voice")
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.set_default_executor(executor)
        return loop.run_until_complete(session.run())
    finally:
        try:
            for task in asyncio.all_tasks(loop):
                task.cancel()
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()
        asyncio.set_event_loop(None)
        # wait=False is the point: do not block on a tool that is still running.
        executor.shutdown(wait=False, cancel_futures=True)


# Longest single instruction. One sentence, not a monologue: the recorder only
# stops early on silence, so this is also how long a turn can be wedged open by
# a noisy room.
MAX_UTTERANCE_SECONDS = listen_local.DEFAULT_MAX_SECONDS

# What this engine needs said that the shared persona does not.
#
# `persona.PERSONA` tells her to act first and narrate after, which is right
# for a model that speaks *while* the tool runs: there an announcement in
# front of the call is pure overhead — and a wrong one, if the call then fails.
#
# Here there is nothing to speak while the tool runs. This pipeline can only
# say what has already been written, so a turn that goes straight to a tool
# call produces no text at all until the tool returns. Measured on this machine
# with `tools/bench_local.py`: a turn that calls a tool took 8.1-9.0s to its
# first spoken sentence; a turn that just answers took 2.6s. Same model, same
# session, same day. The gap is silence.
#
# So the announcement is not overhead here, it is the only thing standing
# between the user and nine seconds of nothing. The half of the original rule
# that was about honesty still stands, and is restated below: the line says
# what you are about to do, never that it is done.
LOCAL_PERSONA = """\
# Speaking while you work

You are speaking out loud through a pipeline that can only say what you have
already written, and it says it as soon as you write it. Nothing is heard
while a tool runs. A turn that goes straight to a tool call is therefore
several seconds of complete silence for the person waiting in the room.

So: before ANY tool call, say one short line about what you are about to do,
then make the call. "Right, switching now." "Let me look." "One moment."

That line must never say the action happened. "Switching to workspace three
now" before the call is right. "Switched to workspace three" before the call
is a lie, and an obvious one the moment the call fails. Report what actually
happened only after the tool has returned.

Both lines are short. They are heard, not read.
"""

# What to say when whisper heard something but `clean()` rejected it. Spoken
# only for audio that was loud enough to record -- a quiet room returns no
# audio at all and gets no answer, which is the difference between "say again"
# and talking to itself all afternoon.
NOT_CAUGHT = "I did not catch that."

# A turn that raises is logged and apologised for, and the loop listens on.
# This many in a row mutes listening instead: something is broken, not
# unlucky. The pause keeps a failure that repeats from spinning hot (#120).
TURN_FAILURES_TO_MUTE = 3
TURN_FAILURE_PAUSE = 2.0

# A spoken confirm or cancel refused, and the way forward (#86). None of them
# names a confirm phrase, or her echo of it would be one.
SOON = "I was still talking. Say it again."
SELF = "I said that word myself, so I cannot take it from the room. Use the key."
BARGE = "With barge-in on I cannot tell your voice from mine. Use the key."
# The word itself is on the screen, never in her mouth.
HELD_PROMPT = "{held} is waiting for you. Say the word on the screen to run it, or cancel."
HELD_PROMPT_BARGE = "{held} is waiting for you. Use the key to run it, or say cancel."


class _Interrupted(Exception):
    """The toggle flipped while the recorder was blocked. Not an error."""


class _ShutForHer(_Interrupted):
    """She is about to speak: the capture is dropped, and it is not silence."""


def brain_for(config: Config, executor: Executor):
    """The warm Claude session, told how to speak on this engine.

    A subclass rather than a second prompt builder: everything
    safety-critical — the policy gate, the blanked API key, the streaming —
    is `WarmBrain`'s and stays `WarmBrain`'s. The only thing added is a
    paragraph about talking, appended last so it wins where it disagrees with
    the shared persona. `_options()` is rebuilt on every `start()`, so this
    survives the session rebuild that `reset_turn` falls back to — which a
    primer sent as a first turn would not.
    """
    from .claude_backend import WarmBrain

    class LocalBrain(WarmBrain):
        def _options(self):
            options = super()._options()
            options.system_prompt = f"{options.system_prompt}\n\n{LOCAL_PERSONA}"
            return options

    brain = LocalBrain(config, executor)
    # With barge_in off, a line the brain has handed over is a line she has
    # said, so an action can wait for the line announcing it (#139).
    brain.speech_first = not config.barge_in
    return brain


class LocalSession:
    """The voice daemon: control socket, bar states, idle stop, one turn at a time.
    """

    def __init__(self, config: Config):
        self.config = config
        self.feedback = Feedback(config)
        # The brain is built on this Executor, so its refusals -- denied, held,
        # dry-run, a crashed or undecided hook -- reach the log through the
        # same sink as everyone else's.
        self.executor = attach_waker(Executor(config, on_action=self._on_action,
                                              on_record=self.feedback.log))
        # This daemon polls watches, so watch_terminal may promise to say (#74).
        self.executor.announces_watches = True
        self.notifications = notifications.Watcher()
        # Built in run(), where there is a loop to start it on. Typed loosely
        # because the import is deliberately late: this engine must remain
        # testable against a fake brain.
        self.brain: Any = None
        # Always starts muted. There is no configuration that changes it.
        self.active = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self._stop = asyncio.Event()
        self._active_event = asyncio.Event()
        # Sentences waiting for a mouth. A queue rather than a task each, so
        # two sentences of the same reply cannot be spoken over each other.
        self._speech: asyncio.Queue[str] = asyncio.Queue()
        # One turn at a time: `listen say` can arrive mid-sentence.
        self._turn_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._last_speech = 0.0
        self._wake_ready = False
        # A resident whisper-server, or None for whisper-cli per utterance (#72).
        self.server = None
        self._user_quit = False
        self._exit_code = 0
        # Finished watches waiting for the gap between two captures (#74).
        self._announcements: list[dict] = []
        # When the last capture first heard something: a spoken confirm is
        # judged by it (#86). Set on the recorder's thread, read after it.
        self._onset: float | None = None
        # The last frame above silence_level in that capture: the user's
        # speech ended here, and the endpoint is timed from it (#80).
        self._last_loud: float | None = None
        # When the latest capture opened, after her echo tail: a wake
        # capture's cap is timed from it (#128).
        self._opened = 0.0
        # When her last pw-cat returned; inf while anything is queued or
        # playing. A spoken confirm starting within the guard of it is refused.
        self._voice_until = 0.0
        self._speaking = False
        # Set while no capture is open: with barge_in off, she waits for it
        # before she speaks (#114).
        self._mic_shut = asyncio.Event()
        self._mic_shut.set()
        # Everything she has said since the latest turn began: a confirm
        # phrase she said herself cannot be taken from the room.
        self._said: list[str] = []

    # -- plumbing -----------------------------------------------------------
    def _on_action(self, name: str, description: str) -> None:
        self.feedback.state("acting", description)
        self.feedback.log(f"action  {description}")

    def _spawn(self, coro) -> None:
        """Run a coroutine in the background without losing it to the GC."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _held(self) -> str | None:
        """What is waiting for a yes, from either gate.

        Our own tools park a re-executable handle in `executor.pending`;
        Claude Code's tools cannot be re-executed from here, so the brain
        holds a description instead. Both are the same thing to the user.
        """
        if self.executor.pending:
            return self.executor.describe(*self.executor.pending)
        return getattr(self.brain, "pending", None)

    def _settle(self) -> None:
        """Put the bar back to whatever the session is actually doing."""
        held = self._held()
        if held:
            self.feedback.state("confirm", held)
            # The word is on the screen, never in her mouth (#86). With
            # barge_in on it is not taken by voice at all.
            how = ("Press the confirm key." if self.config.barge_in else
                   f'Say "{self.config.confirm_words[0]}", or press the confirm key.')
            self.feedback.notify("Waiting for confirmation", f"{held}\n{how}",
                                 urgency="normal")
        else:
            self.feedback.state("listening" if self.active else "idle")

    # -- mouth --------------------------------------------------------------
    async def _speech_loop(self) -> None:
        while not self._stop.is_set():
            text = await self._speech.get()
            self._speaking = True
            # Here, not in Feedback: every mouth is timed, a test's too (#79).
            span = (trace.mark(trace_mod.SPEAK)
                    if (trace := self.feedback.trace) else None)
            try:
                # `Feedback.speak` is fire-and-forget and refuses to speak while
                # the microphone is open, both of which are wrong here: this is
                # the daemon's only voice, and the mic reopens when it returns.
                # `_speak_now` is the same ElevenLabs-then-piper fallback,
                # awaited — so there is still one implementation of it.
                await asyncio.to_thread(self.feedback._speak_now, text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a dead voice must not end the session
                self.feedback.log(f"warn    tts: {type(exc).__name__}: {exc}")
            finally:
                self._speaking = False
                if span:
                    span.close()
                # Before task_done, so whoever join()s sees it.
                if self._speech.empty():
                    self._voice_until = time.monotonic()
                self._speech.task_done()

    async def _say(self, text: str) -> None:
        """Speak one sentence, and wait for it unless barge-in is on.

        The wait is the microphone gate, and with barge_in off it runs both
        ways: she waits for an open capture to shut before she speaks (#114),
        and nothing else in this session runs while she is talking, so her
        voice cannot come back in as the next instruction. For an action that
        holds through the brain's wait (`speech_first`, #139): the call the
        line announces is decided only once this returns. Reads still overlap.
        """
        self.feedback.log(f"say     {text}")
        self._voice_until = math.inf
        self._said.append(text)
        if not self.config.barge_in:
            # The capture shuts on its next frame once `_voice_until` is inf.
            # Bounded: `_record` drops her frames whether or not this waits.
            try:
                await asyncio.wait_for(self._mic_shut.wait(), 1.0)
            except TimeoutError:
                self.feedback.log("warn    mic did not shut in 1s")
        await self._speech.put(text)
        if not self.config.barge_in:
            await self._speech.join()

    def _drop_queued_speech(self) -> None:
        """Toggling off stops her mid-reply, as far as anything here can."""
        if not self._speaking:
            # Nothing will return from pw-cat to reset the clock (#86).
            self._voice_until = time.monotonic()
        while True:
            try:
                self._speech.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._speech.task_done()

    # -- ears ---------------------------------------------------------------
    async def _record(self, max_seconds: float, hang: float) -> bytes | None:
        """One utterance, abandoned if the toggle flips under us.

        `record_utterance` owns its recorder and blocks for as long as the room
        is quiet, so `stop` would otherwise take up to `max_seconds` to be felt.
        The level callback is the only place this code runs during a capture,
        so it is also where the capture is abandoned — and it feeds the orb on
        the way past. None is a capture shut because she is about to speak:
        not silence, and nothing heard (#114).
        """
        if not self.config.barge_in:
            # Her voice is still in the room after playback ends: speakers
            # lag, and the reflection takes a moment to die. Reopening the
            # microphone on the tail of her own last syllable is a bug this
            # project has already been bitten by. Timed from when she stopped,
            # whoever made her speak (#114).
            while True:
                if self._voice_until == math.inf:
                    if self._speaking or not self._speech.empty():
                        await self._speech.join()
                    else:
                        # `_say` is between setting inf and queueing: let it.
                        await asyncio.sleep(0)
                    continue
                delay = self._voice_until + ECHO_TAIL_SECONDS - time.monotonic()
                if delay <= 0:
                    break
                await asyncio.sleep(delay)
        wanted = self.active
        self._onset = None
        self._last_loud = None

        def watch(value: float) -> None:
            if self._onset is None and value > self.config.silence_level:
                self._onset = time.monotonic()
            if value > self.config.silence_level:
                self._last_loud = time.monotonic()
            self.feedback.level(value)
            if self._stop.is_set() or self.active != wanted:
                raise _Interrupted
            if not self.config.barge_in and self._voice_until == math.inf:
                raise _ShutForHer

        self.feedback.mic_open = True
        self._opened = time.monotonic()
        self._mic_shut.clear()
        try:
            return await asyncio.to_thread(
                listen_local.record_utterance, self.config.device,
                self.config.silence_level, hang, max_seconds, watch)
        except _ShutForHer:
            return None
        except _Interrupted:
            return b""
        finally:
            self.feedback.mic_open = False
            self._mic_shut.set()
            self.feedback.level(0.0)

    async def _turn(self) -> None:
        """One instruction, start to finish."""
        pcm = await self._record(MAX_UTTERANCE_SECONDS,
                                 self.config.end_of_speech_seconds)
        if self._stop.is_set() or not self.active:
            return
        if pcm is None:
            return
        if not pcm:
            await self._idle_stop()
            return
        self._last_speech = time.monotonic()
        try:
            text, trace = await self._hear(pcm, self.config.end_of_speech_seconds)
        except listen_local.Unavailable as exc:
            # No transcriber means no instructions, ever. Say so once and stop
            # listening rather than recording into a hole.
            self.feedback.log(f"error   transcribe: {exc}")
            await self._say("I cannot make out speech on this machine right now.")
            await self._set_active(False)
            return
        if not text:
            # `clean()` rejected it: whisper's confident guess at a door
            # closing. It must never reach the brain — that is how "Thank you."
            # becomes an instruction someone gave.
            self.feedback.log("heard   nothing usable")
            await self._say(NOT_CAUGHT)
            return
        if not await self._consent(text, self._heard_at()):
            await self._answer(text, trace)

    async def _idle_stop(self) -> None:
        """Switch listening off after a long enough silence.

        Listening is a mode you enter, not one you hold, and the failure it
        invites is leaving it on. Nothing is being uploaded here, but a
        microphone that stays open for the rest of the afternoon is still a
        microphone that stays open.
        """
        limit = self.config.idle_stop_seconds
        if limit <= 0:
            return
        idle = time.monotonic() - self._last_speech
        if idle < limit:
            return
        self.feedback.log(f"idle    nothing said for {idle:.0f}s — stopping capture")
        self.feedback.notify("Sleeping", f"No speech for {int(idle // 60)} min.")
        await self._set_active(False)

    async def _wake_turn(self) -> None:
        """Listen locally for the wake word while listening is off.

        The audio goes to whisper.cpp on this CPU and nowhere else, and it is
        only transcribed once someone has actually spoken — the recorder
        returns nothing for a silent room, so a quiet house costs one blocked
        read and no CPU.
        """
        try:
            hold = self.config.end_of_speech_seconds
            pcm = await self._record(self.config.wake_max_seconds, hold)
            # Past the cap means it was cut off, not finished: the end of the
            # instruction may be missing, so it only wakes listening (#72).
            # Timed from when the microphone opened, not from when the wait
            # for her began (#128).
            capped = (time.monotonic() - self._opened
                      >= self.config.wake_max_seconds)
            if not pcm or self.active or self._stop.is_set():
                return
            text, trace = await self._hear(pcm, hold)
            if not text:
                return
            if listen_local.heard_wake_word(text, self.config.wake_word):
                rest = listen_local.after_wake_word(text, self.config.wake_word)
                await self._set_active(True)
                if rest and not capped:
                    # "Oma, turn it down" in one breath: do it, rather than
                    # wake up and make them say it again.
                    self.feedback.log(f"wake    heard {text!r} — acting on {rest!r}")
                    self._last_speech = time.monotonic()
                    if not await self._consent(rest, self._heard_at()):
                        await self._answer(rest, trace)
                else:
                    self.feedback.log(f"wake    heard {text!r}")
            else:
                # Logged, because the only way to tune a wake word is to see
                # what the transcriber actually returns for it.
                self.feedback.log(f"wake    ignored {text!r}")
        except asyncio.CancelledError:
            raise
        except listen_local.Unavailable as exc:
            self.feedback.log(f"warn    wake word off: {exc}")
            self._wake_ready = False
        except Exception as exc:  # never take the session down over this
            self.feedback.log(f"warn    wake: {type(exc).__name__}: {exc}")
            await asyncio.sleep(2.0)

    # -- the brain ----------------------------------------------------------
    async def _hear(self, pcm: bytes, hold: float):
        """Transcribe what was just recorded, and start the task's clock.

        The trace starts when the user stopped talking, not when the model is
        asked: the hold that decided the sentence was over and whisper's pass
        over it are both time they wait through (#72). The ENDPOINT span is
        measured from the capture's last loud frame, so it is the hold, up to
        one frame, and pw-record's teardown (#80). #114's echo tail runs
        before the capture opens and is never in it. A recorder that never
        reported a level falls back to `hold`.
        """
        now = time.monotonic()
        task = None
        if self.config.trace_timings:
            loud = self._last_loud
            start = loud if loud is not None and loud <= now else now - hold
            task = trace_mod.Trace(started=start, hold=hold)
            task.spans.append(trace_mod.Span(trace_mod.ENDPOINT, "", start, now))
        was_resident = self.server is not None and self.server.alive
        span = task.mark(trace_mod.TRANSCRIBE) if task else None
        try:
            text = await asyncio.to_thread(
                listen_local.transcribe, pcm, self.config, server=self.server)
        finally:
            if span:
                span.close()
        if was_resident and not self.server.alive:
            self.feedback.log("warn    transcribe: whisper-server failed — using whisper-cli")
        return text, task

    async def _answer(self, text: str, trace=None, *,
                      release: str | None = None, from_user: bool = True) -> None:
        """Think about one sentence, and speak the reply as it arrives.

        Every sentence goes out the moment the brain finishes it. Collecting
        the reply and speaking it at the end would put the whole thinking time
        in front of the first word, which is the entire latency argument for
        this design.

        `release` is the held action's description when `text` is the brain's
        release message rather than something the user said (#76).
        A measured fixed command is run without the model first (#71), but
        never on a release turn or an announcement: neither is something
        the user said, and both must reach the brain unchanged.
        `from_user=False` is a finished watch, which nobody said either: the
        watcher has already logged it (#74).
        """
        async with self._turn_lock:
            # After the lock, not before: a turn queued behind it must not
            # wipe what the running one is still saying (#86).
            self._said = []
            if release is not None:
                self.feedback.log(f"release {release}")
            elif from_user:
                self.feedback.log(f"heard   {text!r}")
            self.feedback.state("thinking")
            held_before = self._held()
            # One task: from here -- the utterance is transcribed and the
            # thinking starts -- to the last spoken sentence. What the user
            # actually waits through.
            task = trace or (trace_mod.Trace() if self.config.trace_timings else None)
            self.executor.trace = self.feedback.trace = task
            # Opened on the model branch only: a routed turn has no model time.
            turn = None
            try:
                hit = (await self._route(text)
                       if release is None and from_user else None)
                if hit:
                    await self._run_route(hit, text)
                else:
                    turn = task.mark(trace_mod.TURN) if task else None
                    async for sentence in self.brain.ask_stream(
                            text, release=release is not None,
                            from_user=from_user):
                        if sentence := sentence.strip():
                            # Model time stops at the sentence: her speaking it
                            # is SPEAK, and the wait for the mic to shut is in
                            # the total and no phase (#79). Whether the next
                            # TURN span is a continuation is the trace's call,
                            # from the tools opened in between.
                            if turn:
                                turn.close()
                            await self._say(sentence)
                            turn = task.mark(trace_mod.TURN) if task else None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # One bad turn is not a reason to lose the daemon. Say what
                # happened — silence is indistinguishable from a crash from
                # the other side of the room — and realign the session so the
                # next turn does not answer this question.
                self.feedback.log(f"error   brain: {type(exc).__name__}: {exc}")
                if turn:
                    turn.close()  # her apology is not model time
                await self._say("Something went wrong with that.")
                await self._reset_turn()
            # Whatever happened above -- a reply, a cancel, an exception --
            # the turn is over, so anything the input helper was holding is
            # released by closing it (#30).
            self.executor.end_turn()
            if turn:
                turn.close()
            if task:
                self.executor.trace = self.feedback.trace = None
                self.feedback.log(task.finish().line())
            if usage := getattr(self.brain, "usage", None):
                self.feedback.log(f"usage   {usage}")
            held = self._held()
            if held and held != held_before:
                # Said here rather than left to the model: this is the gate
                # that stops a misheard sentence rebooting the machine, and it
                # cannot depend on the reply happening to mention it.
                prompt = HELD_PROMPT_BARGE if self.config.barge_in else HELD_PROMPT
                await self._say(prompt.format(held=held))
            self._settle()

    async def _route(self, text: str) -> router.Route | None:
        """The fixed command `text` is, if it is exactly one (#71).

        Not while something waits for a yes: "confirm" and "cancel" are the
        gate's words, and a routed action would be a second one behind it. Off
        the loop, because the window list is a `hyprctl` with a 5 s timeout.
        A router that breaks gives the turn to the model, which is where every
        turn went before it existed.
        """
        if not self.config.router or self._held() is not None:
            return None
        try:
            return await asyncio.to_thread(
                router.route, text, lambda: self.executor._query_rows("clients"),
                self.config.wake_word)
        except Exception as exc:
            self.feedback.log(f"warn    router: {type(exc).__name__}: {exc}")
            return None

    async def _run_route(self, hit: router.Route, text: str) -> None:
        """Run one routed command through the Executor, and say the result.

        Through `Executor.call`, so the policy gate judges it exactly as it
        would the model's call. The failure lines the Executor writes are for
        the model ("Tell the user you will not do that", "Stop here and ask"),
        so only their first sentence is spoken, and a hold speaks nothing of
        its own: the turn's tail already says what needs confirming.
        """
        if hit.tool is None:
            self.feedback.log("routed  window list")
            await self._say(hit.answer or "")
            self.brain.note(f'User said "{text}" → window list → answered')
            return
        description = self.executor.describe(hit.tool, hit.args)
        self.feedback.log(f"routed  {description}")
        result = await asyncio.to_thread(self.executor.call, hit.tool, hit.args)
        if result.output.startswith("[dry-run]"):
            line, outcome = result.output, "dry-run"
        elif result.ok:
            line, outcome = hit.said, "ok"
        elif self.executor.pending == (hit.tool, hit.args):
            line, outcome = "", "held for confirmation"
        else:
            first = result.output.split(". ")[0].strip()
            line, outcome = first, f"failed: {first}"
        if line:
            await self._say(line)
        self.brain.note(f'User said "{text}" → {description} → {outcome}')

    async def _reset_turn(self) -> None:
        try:
            await self.brain.reset_turn()
        except Exception as exc:  # best effort; the session may be gone
            self.feedback.log(f"warn    reset: {type(exc).__name__}: {exc}")

    # -- the loop -----------------------------------------------------------
    async def _listen_loop(self) -> None:
        """One recorder, three states: listening, waiting for a word, asleep.

        Deliberately one loop rather than two. There is exactly one microphone, and two coroutines that can both open it is a
        way to hear half of everything.
        """
        failures = 0
        while not self._stop.is_set():
            try:
                if self.active and self._announcements:
                    # Between captures, never into one. ponytail: the worst
                    # case is one whole capture (15s in a silent room); if that
                    # matters, end a capture that has heard nothing yet.
                    await self._answer(
                        watch_message(self._announcements.pop(0)),
                        from_user=False)
                    failures = 0
                    continue  # a mute or stop during it is seen before the mic opens
                if self.active:
                    await self._turn()
                elif self.config.wake_word and self._wake_ready:
                    await self._wake_turn()
                else:
                    await self._wait_for_toggle()
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one bad turn must not end listening
                self.feedback.log(f"error   turn: {type(exc).__name__}: {exc}")
                failures += 1
                if failures < TURN_FAILURES_TO_MUTE:
                    await self._say("Something went wrong with that.")
                else:
                    # Muted first: muting drops queued speech, so with barge-in
                    # on, saying it first would drop it unsaid.
                    await self._set_active(False)
                    await self._say("Listening keeps failing, so I have "
                                    "stopped. The log says why.")
                await asyncio.sleep(TURN_FAILURE_PAUSE)

    async def _watch_loop(self) -> None:
        """Queue a finished watch for the listen loop, or notify if muted.

        It never speaks: only the listen loop knows when the microphone is
        shut (#74).
        """
        while not self._stop.is_set():
            try:
                await asyncio.sleep(WATCH_POLL_SECONDS)
                for job in await asyncio.to_thread(self.executor.poll_watches):
                    headline = watch_headline(job)
                    self.feedback.log(f"watch   {job['target']}: {headline}")
                    if self.active:
                        self._announcements.append(job)
                    else:
                        self.feedback.notify("Oma", headline)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a watcher must never take the session down
                self.feedback.log(f"warn    watcher: {type(exc).__name__}: {exc}")

    async def _wait_for_toggle(self) -> None:
        waiter = asyncio.create_task(self._active_event.wait())
        stopper = asyncio.create_task(self._stop.wait())
        _, pending = await asyncio.wait(
            {waiter, stopper}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()

    # -- control socket -----------------------------------------------------
    def _control(self, command: str) -> str:
        """Called on the ControlServer thread; hands work to the event loop."""
        if self.loop is None:
            return "not ready"
        verb, _, rest = command.partition(" ")
        if verb == "toggle":
            future = asyncio.run_coroutine_threadsafe(self._toggle(), self.loop)
        elif verb in ("start", "stop"):
            future = asyncio.run_coroutine_threadsafe(
                self._set_active(verb == "start"), self.loop)
        elif verb == "say":
            future = asyncio.run_coroutine_threadsafe(self._inject(rest), self.loop)
        elif verb == "confirm":
            future = asyncio.run_coroutine_threadsafe(self._local_confirm(), self.loop)
        elif verb == "cancel":
            future = asyncio.run_coroutine_threadsafe(self._local_cancel(), self.loop)
        elif verb == "quit":
            self._user_quit = True
            self.loop.call_soon_threadsafe(self._stop.set)
            return "stopping"
        else:
            return f"unknown command {verb!r}"
        try:
            return future.result(timeout=10)
        except Exception as exc:
            return f"error: {type(exc).__name__}: {exc}"

    async def _toggle(self) -> str:
        return await self._set_active(not self.active)

    async def _set_active(self, active: bool) -> str:
        self.active = active
        if active:
            self._last_speech = time.monotonic()
            self._active_event.set()
        else:
            self._active_event.clear()
            self._drop_queued_speech()
            # Muted before its turn came: a notification, as when muted.
            for job in self._announcements:
                self.feedback.notify("Oma", watch_headline(job))
            self._announcements.clear()
        self.feedback.state("listening" if active else "idle")
        self.feedback.notify("Listening" if active else "Sleeping")
        self.feedback.log(f"gate    {'listening' if active else 'muted'}")
        return "listening" if active else "idle"

    async def _inject(self, text: str) -> str:
        """`omarchy-voice listen say ...` — a typed turn, mic untouched.

        Answered in the background: a spoken reply takes as long as it takes to
        say, and the caller is a keybinding that should not sit there holding
        the socket open through it.
        """
        text = text.strip()
        if not text:
            return "nothing to say"
        self.feedback.log(f"typed   {text!r}")
        self._spawn(self._typed(text))
        return "sent"

    async def _typed(self, text: str) -> None:
        """A typed turn: consent first, as the user at the keyboard (#86)."""
        if not await self._consent(text, None):
            await self._answer(text)

    async def _consent(self, text: str, onset: float | None) -> bool:
        """Take `text` as the answer to what is held, if it is one (#86).

        The engine decides, not the model: a whole-utterance confirm or cancel
        is consumed here and never reaches the brain, whether it is taken or
        refused. `onset` is when the capture first heard it; None is a typed
        turn. True means the utterance was consumed.
        """
        if self._held() is None:
            return False
        confirm = _matches(text, self.config.confirm_words, allow_negation=False)
        if not confirm and not _matches(text, self.config.cancel_words,
                                        allow_negation=False):
            return False
        if onset is not None:
            refusal = None
            if confirm and self.config.barge_in:
                # Her voice and the user's are one stream then; cancel is
                # still heard, because cancelling is safe.
                refusal = ("barge-in", BARGE)
            elif onset - self._voice_until < self.config.spoken_confirm_guard_seconds:
                refusal = ("too soon", SOON)
            elif confirm and any(_normalize(confirm) in _normalize(line)
                                 for line in self._said):
                refusal = ("said it", SELF)
            if refusal:
                self.feedback.log(f"consent  refused ({refusal[0]}) {text!r}")
                await self._say(refusal[1])
                return True
        if confirm:
            await self._release(wait=True, said=text)
            return True
        held = self._held()
        await self._local_cancel()
        await self._say(f"Cancelled. {held} was not run.")
        return True

    def _heard_at(self) -> float:
        """The last capture's onset. Audio always has one; if it somehow does
        not, it is treated as too soon rather than as typed."""
        return self._onset if self._onset is not None else -math.inf

    async def _local_confirm(self) -> str:
        """Keybind / CLI confirm — does not trust the model, or the transcript."""
        return await self._release(wait=False)

    async def _release(self, wait: bool, said: str = "") -> str:
        """Run what is held: the one release path for the key and the voice.

        `wait` is the spoken path (#86): the release turn is awaited, so with
        barge_in off the microphone stays shut through it and its echo tail.
        """
        how = "spoken" if wait else "local"
        if self.executor.pending:
            held = self.executor.describe(*self.executor.pending)
            self.feedback.log(f"confirm {how} release: {held}")
            result = await asyncio.to_thread(self.executor.run_pending)
            if wait:
                # Said, as a routed command's result is: nobody else will.
                if result.output.startswith("[dry-run]"):
                    line, outcome = result.output, "dry-run"
                elif result.ok:
                    line, outcome = "Done.", "ok"
                else:
                    line = result.output.split(". ")[0].strip()
                    outcome = f"failed: {line}"
                if line:
                    await self._say(line)
                self.brain.note(f'User said "{said}" → {held} → {outcome}')
            self._settle()
            return result.as_tool_result()
        held = getattr(self.brain, "pending", None)
        if not held:
            return "nothing to confirm"
        text = self.brain.confirm()
        self.feedback.log(f"confirm {how} release: {held}")
        # A Claude Code tool call has no re-executable handle here, so the
        # brain asks the model to make that one call, and its gate allows
        # nothing else in that turn. Not the utterance again: that ran the
        # rest of it twice, or ran whatever was said last (#76).
        if wait:
            await self._answer(text, release=held)
        else:
            self._spawn(self._answer(text, release=held))
        self._settle()
        return f"Confirmed: {held}"

    async def _local_cancel(self) -> str:
        # Not only `pending`: a confirmed action still waiting for its release
        # turn has no hold left, and cancelling it must withdraw the yes (#76).
        brain_holds = (getattr(self.brain, "held_or_approved", None)
                       or getattr(self.brain, "pending", None))
        held = self.executor.drop_pending() or (
            self.brain.cancel() if brain_holds else None)
        if held is None:
            return "nothing to cancel"
        self.feedback.log(f"cancel  {held}")
        self._settle()
        return f"Cancelled: {held}. It was not run."

    # -- main loop ----------------------------------------------------------
    async def run(self) -> int:
        self.loop = asyncio.get_running_loop()
        self.brain = brain_for(self.config, self.executor)
        control = ControlServer(self._control)
        control.start()
        # Started here rather than lazily on first use: a notification can only
        # be recorded while it is happening, and the question about it always
        # arrives afterwards.
        if self.config.allow_notifications:
            if problem := self.notifications.start():
                self.feedback.log(f"warn    {problem}")
        if self.config.wake_word:
            if problems := listen_local.check_ready(self.config):
                for problem in problems:
                    self.feedback.log(f"warn    wake word off: {problem}")
            else:
                self._wake_ready = True
                self.feedback.log(
                    f"wake    listening locally for {self.config.wake_word!r}")
        speech = asyncio.create_task(self._speech_loop())
        watcher = asyncio.create_task(self._watch_loop())
        self.feedback.state("idle")
        self.feedback.log(f"start   engine=local stt=whisper.cpp "
                          f"brain={self.config.claude_model} "
                          f"voice={voice_chain(self.config) or 'none'} "
                          f"dry_run={self.config.dry_run}")
        # No key named here: the binding lives in the user's bindings.lua and
        # this process cannot see it.
        self.feedback.log("gate    muted — the voice toggle key starts listening")

        # Loaded while the brain warms up, which hides it: 0.3 s against 6.5.
        hearing = asyncio.ensure_future(
            asyncio.to_thread(listen_local.Server.start, self.config))
        try:
            # At login, not on the first sentence. `start()` spends a throwaway
            # turn opening the session -- 6.5s on this machine -- and buys back
            # roughly two seconds on every turn after it. Nobody is waiting
            # now; at the first "Oma" everybody is.
            self.feedback.state("thinking", "waking up")
            self.feedback.log("start   warming the Claude session — "
                              "the toggle works once this finishes")
            await self.brain.start()
            self.server = await hearing
            self.feedback.state("idle")
            self.feedback.log("start   brain ready")
            self.feedback.log("start   whisper resident" if self.server
                              else "start   whisper per utterance")
            await self._listen_loop()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.feedback.state("error", str(exc))
            self.feedback.log(f"error   {type(exc).__name__}: {exc}")
            print(f"local session failed: {type(exc).__name__}: {exc}")
            self._exit_code = 1
        finally:
            self._stop.set()
            speech.cancel()
            watcher.cancel()
            for task in list(self._tasks):
                task.cancel()
            await self.brain.stop()
            # Still starting if the brain failed first; it must not outlive us.
            server = self.server or await hearing
            if server:
                await asyncio.to_thread(server.stop)
            await asyncio.to_thread(control.stop)
            await asyncio.to_thread(self.notifications.stop)
            self.feedback.state("idle")
        return 0 if self._user_quit else self._exit_code


# --- readiness --------------------------------------------------------------

def voice_chain(config: Config) -> str:
    """How a reply will actually be spoken, or "" if it cannot be.

    In the order `Feedback._speak_now` tries them, so this is a description of
    what will happen rather than a list of what is installed.
    """
    if config.tts_command:
        return f"tts_command ({config.tts_command.split()[0]})"
    local = ""
    if feedback_mod.piper_model() and shutil.which("piper"):
        local = "piper"
    elif shutil.which("espeak-ng"):
        local = "espeak-ng"
    if elevenlabs.ready(config):
        return (f"ElevenLabs {config.elevenlabs_model}"
                + (f", {local} if it fails" if local else " (no local fallback)"))
    return local


def check_ready(config: Config) -> list[str]:
    """Everything standing between here and a working local turn."""
    problems = list(listen_local.check_ready(config))
    try:
        from . import claude_backend as cb
        problems += cb.check_ready(config)
    except ImportError as exc:  # pragma: no cover - the module is in-tree
        problems.append(f"the claude backend is not importable ({exc})")
    if not voice_chain(config):
        problems.append(
            "no voice: ElevenLabs is off or unconfigured and neither piper nor "
            "espeak-ng is installed, so a reply has no way to be heard")
    return problems


def run(config: Config) -> int:
    """Entry point used by `omarchy-voice run`.

    Being offline is not a crash here: whisper and piper both run on this machine, and only the brain needs the network. It
    still refuses to start without one, because a daemon that can hear and
    speak but not think is a microphone with a light on it.
    """
    problems = check_ready(config)
    soft = ("cannot reach api.anthropic.com",)
    hard = [p for p in problems if not any(s in p for s in soft)]
    if hard:
        for problem in hard:
            print(f"cannot start local engine: {problem}")
        feedback = Feedback(config)
        feedback.state("unconfigured", hard[0])
    # The bar mark is easy to miss on a fresh install, which is exactly when
    # this happens. One notification, and the answer to "why does voice do
    # nothing" is one command away (#18).
        feedback.notify("omarchy-voice", "voice has no backend: run `omarchy-voice doctor`")
        # Exit 0: these are all "not set up yet", and a restart loop on a fresh
        # install looks identical to a broken daemon from the outside.
        return 0
    for problem in problems:
        print(f"warning: {problem}")
    try:
        return _run_until_done(LocalSession(config))
    except KeyboardInterrupt:
        return 0
