"""The local engine: heard here, thought on your plan, spoken in her voice.

Nothing in this file is new. It is the parts this repo already had, wired into
the loop the OpenAI Realtime session used to be:

    toggle / wake word ─▶ pw-record ─▶ whisper.cpp ─▶ Claude (warm) ─▶ sentence
        session.py        listen_local  listen_local   claude_backend     │
                                                                         ▼
                                          pw-cat ◀── ElevenLabs / piper (feedback)

What is lost against `realtime.py` is real: that engine hears tone, not words,
and it can be interrupted mid-sentence because the microphone never closes.
This one gets a transcript — a flat sentence with the sarcasm removed — and
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
import shutil
import time
from typing import Any

from . import elevenlabs, feedback as feedback_mod, listen_local, notifications
from . import trace as trace_mod
from .config import Config
from .feedback import Feedback
# Both borrowed from the engine this replaces rather than copied: the echo tail
# was measured on this machine, and the runner exists because a tool still
# blocked in a worker thread must not be able to wedge the exit.
from .realtime import ECHO_TAIL_SECONDS, _run_until_done
from .session import ControlServer
from .tools import attach_waker, Executor

# Longest single instruction. One sentence, not a monologue: the recorder only
# stops early on silence, so this is also how long a turn can be wedged open by
# a noisy room.
MAX_UTTERANCE_SECONDS = listen_local.DEFAULT_MAX_SECONDS

# What this engine needs said that the realtime one does not.
#
# `REALTIME_PERSONA` tells her to act first and narrate after, and for
# speech-to-speech that is right: OpenAI speaks *while* the tool runs, so an
# announcement in front of the call is pure overhead — and a wrong one, if the
# call then fails.
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


class _Interrupted(Exception):
    """The toggle flipped while the recorder was blocked. Not an error."""


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

    return LocalBrain(config, executor)


class LocalSession:
    """`RealtimeSession`'s shape, with no websocket underneath it.

    Same control socket verbs, same bar states, same silence gate and idle
    stop — the outside cannot tell which engine is running except by the log.
    """

    def __init__(self, config: Config):
        self.config = config
        self.feedback = Feedback(config)
        # The brain is built on this Executor, so its refusals -- denied, held,
        # dry-run, a crashed or undecided hook -- reach the log through the
        # same sink as everyone else's.
        self.executor = attach_waker(Executor(config, on_action=self._on_action,
                                              on_record=self.feedback.log))
        self.notifications = notifications.Watcher()
        # Built in run(), where there is a loop to start it on. Typed loosely
        # because the import is deliberately late: this engine must remain
        # testable against a fake brain.
        self.brain: Any = None
        # Always starts muted, exactly as the realtime engine does. There is no
        # configuration that changes it.
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
        # The last thing the user said, so a confirmed action can be replayed.
        self._last_text = ""
        self._last_speech = 0.0
        self._wake_ready = False
        self._user_quit = False
        self._exit_code = 0

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
            self.feedback.notify("Waiting for confirmation", held, urgency="normal")
        else:
            self.feedback.state("listening" if self.active else "idle")

    # -- mouth --------------------------------------------------------------
    async def _speech_loop(self) -> None:
        while not self._stop.is_set():
            text = await self._speech.get()
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
                self._speech.task_done()

    async def _say(self, text: str) -> None:
        """Speak one sentence, and wait for it unless barge-in is on.

        The wait is the microphone gate. With barge_in off, nothing else in
        this session runs while she is talking, so her voice cannot come back
        in as the next instruction.
        """
        self.feedback.log(f"say     {text}")
        await self._speech.put(text)
        if not self.config.barge_in:
            await self._speech.join()

    def _drop_queued_speech(self) -> None:
        """Toggling off stops her mid-reply, as far as anything here can."""
        while True:
            try:
                self._speech.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._speech.task_done()

    # -- ears ---------------------------------------------------------------
    async def _record(self, max_seconds: float, hang: float) -> bytes:
        """One utterance, abandoned if the toggle flips under us.

        `record_utterance` owns its recorder and blocks for as long as the room
        is quiet, so `stop` would otherwise take up to `max_seconds` to be felt.
        The level callback is the only place this code runs during a capture,
        so it is also where the capture is abandoned — and it feeds the orb on
        the way past.
        """
        wanted = self.active

        def watch(value: float) -> None:
            self.feedback.level(value)
            if self._stop.is_set() or self.active != wanted:
                raise _Interrupted

        self.feedback.mic_open = True
        try:
            return await asyncio.to_thread(
                listen_local.record_utterance, self.config.device,
                self.config.silence_level, hang, max_seconds, watch)
        except _Interrupted:
            return b""
        finally:
            self.feedback.mic_open = False
            self.feedback.level(0.0)

    async def _turn(self) -> None:
        """One instruction, start to finish."""
        pcm = await self._record(MAX_UTTERANCE_SECONDS,
                                 self.config.silence_hold_seconds)
        if self._stop.is_set() or not self.active:
            return
        if not pcm:
            await self._idle_stop()
            return
        self._last_speech = time.monotonic()
        try:
            text = await asyncio.to_thread(
                listen_local.transcribe, pcm, self.config)
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
        await self._answer(text)

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
            pcm = await self._record(self.config.wake_max_seconds,
                                     listen_local.DEFAULT_HANG_SECONDS)
            if not pcm or self.active or self._stop.is_set():
                return
            text = await asyncio.to_thread(
                listen_local.transcribe, pcm, self.config)
            if not text:
                return
            if listen_local.heard_wake_word(text, self.config.wake_word):
                self.feedback.log(f"wake    heard {text!r}")
                await self._set_active(True)
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
    async def _answer(self, text: str) -> None:
        """Think about one sentence, and speak the reply as it arrives.

        Every sentence goes out the moment the brain finishes it. Collecting
        the reply and speaking it at the end would put the whole thinking time
        in front of the first word, which is the entire latency argument for
        this design.
        """
        async with self._turn_lock:
            self._last_text = text
            self.feedback.log(f"heard   {text!r}")
            self.feedback.state("thinking")
            held_before = self._held()
            # One task: from here -- the utterance is transcribed and the
            # thinking starts -- to the last spoken sentence. What the user
            # actually waits through.
            task = trace_mod.Trace() if self.config.trace_timings else None
            self.executor.trace = task
            turn = task.mark(trace_mod.TURN) if task else None
            try:
                async for sentence in self.brain.ask_stream(text):
                    if sentence := sentence.strip():
                        # A sentence arriving means the model came back. If it
                        # calls a tool and comes back again, that second return
                        # is a continuation -- the round trip a tool result
                        # cost. Several tools in one response still only get
                        # here once, which is the distinction the metric rests
                        # on.
                        if task and turn:
                            turn.close()
                            turn = task.mark(trace_mod.TURN)
                        await self._say(sentence)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # One bad turn is not a reason to lose the daemon. Say what
                # happened — silence is indistinguishable from a crash from
                # the other side of the room — and realign the session so the
                # next turn does not answer this question.
                self.feedback.log(f"error   brain: {type(exc).__name__}: {exc}")
                await self._say("Something went wrong with that.")
                await self._reset_turn()
            if turn:
                turn.close()
            if task:
                self.executor.trace = None
                self.feedback.log(task.finish().line())
            if usage := getattr(self.brain, "usage", None):
                self.feedback.log(f"usage   {usage}")
            held = self._held()
            if held and held != held_before:
                # Said here rather than left to the model: this is the gate
                # that stops a misheard sentence rebooting the machine, and it
                # cannot depend on the reply happening to mention it.
                await self._say(f"{held} needs confirming. Say confirm, or cancel.")
            self._settle()
            if not self.config.barge_in:
                # Her voice is still in the room after playback ends: speakers
                # lag, and the reflection takes a moment to die. Reopening the
                # microphone on the tail of her own last syllable is a bug this
                # project has already been bitten by.
                await asyncio.sleep(ECHO_TAIL_SECONDS)

    async def _reset_turn(self) -> None:
        try:
            await self.brain.reset_turn()
        except Exception as exc:  # best effort; the session may be gone
            self.feedback.log(f"warn    reset: {type(exc).__name__}: {exc}")

    # -- the loop -----------------------------------------------------------
    async def _listen_loop(self) -> None:
        """One recorder, three states: listening, waiting for a word, asleep.

        Deliberately one loop rather than the realtime engine's two. There is
        exactly one microphone, and two coroutines that can both open it is a
        way to hear half of everything.
        """
        while not self._stop.is_set():
            if self.active:
                await self._turn()
            elif self.config.wake_word and self._wake_ready:
                await self._wake_turn()
            else:
                await self._wait_for_toggle()

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
        self._spawn(self._answer(text))
        return "sent"

    async def _local_confirm(self) -> str:
        """Keybind / CLI confirm — does not trust the model, or the transcript."""
        if self.executor.pending:
            held = self.executor.describe(*self.executor.pending)
            self.feedback.log(f"confirm local release: {held}")
            result = await asyncio.to_thread(self.executor.run_pending)
            self._settle()
            return result.as_tool_result()
        held = getattr(self.brain, "pending", None)
        if not held:
            return "nothing to confirm"
        self.brain.confirm()
        self.feedback.log(f"confirm local release: {held}")
        # A Claude Code tool call has no re-executable handle here, so the
        # instruction is replayed with that exact action pre-approved.
        self._spawn(self._answer(self._last_text))
        self._settle()
        return f"Confirmed: {held}"

    async def _local_cancel(self) -> str:
        held = self.executor.drop_pending() or (
            self.brain.cancel() if getattr(self.brain, "pending", None) else None)
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
        self.feedback.state("idle")
        self.feedback.log(f"start   engine=local stt=whisper.cpp "
                          f"brain={self.config.claude_model} "
                          f"voice={voice_chain(self.config) or 'none'} "
                          f"dry_run={self.config.dry_run}")
        # No key named here: the binding lives in the user's bindings.lua and
        # this process cannot see it.
        self.feedback.log("gate    muted — the voice toggle key starts listening")

        try:
            # At login, not on the first sentence. `start()` spends a throwaway
            # turn opening the session -- 6.5s on this machine -- and buys back
            # roughly two seconds on every turn after it. Nobody is waiting
            # now; at the first "Oma" everybody is.
            self.feedback.state("thinking", "waking up")
            self.feedback.log("start   warming the Claude session — "
                              "the toggle works once this finishes")
            await self.brain.start()
            self.feedback.state("idle")
            self.feedback.log("start   brain ready")
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
            for task in list(self._tasks):
                task.cancel()
            await self.brain.stop()
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
    """Entry point used by `omarchy-voice run` with engine = "local".

    Being offline is not a crash here, unlike the realtime engine: whisper and
    piper both run on this machine, and only the brain needs the network. It
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
