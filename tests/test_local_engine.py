"""The local engine: whisper in, Claude in the middle, her voice out.

The realtime engine was one websocket doing everything, and its failure modes
were all about the wire. This one is four local parts in a row, and its failure
modes are all about the seams between them: noise reaching the brain, the reply
arriving in one lump after the thinking is over, her own voice coming back in
as the next instruction, and one bad turn taking the daemon with it.

Everything here is faked -- no microphone, no whisper, no network, no speakers.
"""

import asyncio
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from eval_router import FIXTURE_CLIENTS
from omarchy_voice import cli, feedback, listen_local, local_engine
from omarchy_voice.claude_backend import WarmBrain
from omarchy_voice.config import Config
from omarchy_voice.tools import Result


class FakeBrain:
    """`WarmBrain`'s shape, with nothing behind it.

    Written against the contract rather than the implementation, so this file
    says what the engine actually needs from a brain: complete sentences, a
    held action, and a way to be realigned after a turn that went wrong.
    """

    def __init__(self, sentences=("Done.",), hold=None, boom=None):
        self.sentences = list(sentences)
        self.hold = hold
        self.boom = boom
        self.pending = None
        self.asked = []
        self.released = []
        self.from_user = []
        self.confirmed = []
        self.reset_turns = 0
        self.notes = []
        self.started = self.stopped = False
        # Set by the test's mouth as soon as the first sentence is SPOKEN, so
        # the generator can tell streaming from batching from the inside.
        self.spoken_first = threading.Event()
        self.streamed_live = None

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def reset_turn(self):
        self.reset_turns += 1

    @property
    def usage(self):
        return {"in": 1, "out": 2, "cost": 0.0, "turns": len(self.asked)}

    def confirm(self):
        held, self.pending = self.pending, None
        self.confirmed.append(held)
        return held

    def cancel(self):
        held, self.pending = self.pending, None
        return held

    def note(self, line):
        self.notes.append(line)

    async def ask_stream(self, text, release=False, from_user=True):
        self.asked.append(text)
        self.released.append(release)
        self.from_user.append(from_user)
        for n, sentence in enumerate(self.sentences):
            if n == 1:
                # Was the first sentence out of the speakers before the second
                # one was even produced? That is the whole point of streaming,
                # and it is only observable from here.
                self.streamed_live = await asyncio.to_thread(
                    self.spoken_first.wait, 2.0)
            yield sentence
        if self.boom:
            raise self.boom
        if self.hold:
            self.pending = self.hold


class Mouth:
    """Stands in for ElevenLabs and piper both. Records, and can be held shut.

    A gated mouth is a barrier, not a slow one. It blocks until the test lets
    it go and appends nothing until then, so "she is still mid-sentence" is
    something the tests below can rely on rather than race against. An earlier
    version spoke anyway after a 5s timeout, which made every assertion about
    what had NOT been said yet a bet on how loaded the machine was.
    """

    def __init__(self, gated=False):
        self.spoken = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.holding = False  # inside __call__: she is mid-sentence right now
        if not gated:
            self.release.set()

    def __call__(self, text):
        self.holding = True
        try:
            self.started.set()
            # Bounded only so a genuine deadlock ends as a failing test rather
            # than a hung suite. A timeout here speaks nothing: it is not a
            # sentence.
            if self.release.wait(30):
                self.spoken.append(text)
        finally:
            self.holding = False

    async def wait_until_speaking(self, case, why=lambda: ""):
        """Block until she is provably mid-sentence.

        Polled on the event loop, not waited on a worker thread: the wait must
        not compete with the mouth itself for the pool (#120).
        """
        case.assertTrue(await case.until(self.started.is_set, 30),
                        "the sentence was never handed to the mouth at all"
                        + why())


class Ears:
    """The fake microphone. Counts captures, and says when one starts again.

    `again` is the whole of the gate test: the microphone reopening is an
    event, so both "it did" and "it did not yet" are questions asked of an
    event rather than of a clock reading taken at an arbitrary instant.

    With `shut` set, every capture after the first is a quiet room -- unless
    the mouth is holding a sentence, when the reopening is flagged in
    `over_her` and the capture blocks until `shut` is released (#120).
    """

    def __init__(self, case):
        self.case = case
        self.captures = 0
        self.again = threading.Event()
        self.over_her = threading.Event()
        self.shut = None

    def __call__(self, *args, **kwargs):
        self.captures += 1
        if self.captures > 1:
            self.again.set()
            if self.shut is not None:
                if self.case.mouth.holding:
                    self.over_her.set()
                    self.shut.wait(30)
                else:
                    threading.Event().wait(0.01)  # a quiet room
                return b""
        return self.case.audio


class OnsetEars(Ears):
    """`Ears` that also say when the speech started: `delay` later.

    The case's stepped clock is moved on by `delay`, then the level callback
    -- the recorder's 5th positional argument -- hears a frame above
    `silence_level`, which is what the engine takes as the onset (#86).
    """

    def __init__(self, case, delay):
        super().__init__(case)
        self.delay = delay

    def __call__(self, *args, **kwargs):
        self.case.now += self.delay
        args[4](args[1] + 1)
        return super().__call__(*args, **kwargs)


class EngineTestCase(unittest.IsolatedAsyncioTestCase):
    """A session with a fake mouth, fake ears and a fake brain."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for name in ("LOG_FILE", "STATE_FILE", "STATE_DIR", "RUNTIME_DIR", "LEVEL_FILE"):
            value = root / name.lower()
            patcher = mock.patch.object(feedback, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    async def asyncSetUp(self):
        self.audio = b"\x01\x02" * 16000
        self.heard = "close the browser"
        # No windows unless a test gives some: with the query failing the
        # router never routes, so "close the browser" still reaches the brain
        # whatever this machine has open.
        self.clients = None

    async def until(self, predicate, seconds=2.0):
        deadline = asyncio.get_running_loop().time() + seconds
        while not predicate() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        return predicate()

    def build(self, brain=None, mouth=None, **overrides):
        config = Config(notify=False, dry_run=True, **overrides)
        session = local_engine.LocalSession(config)
        session.loop = asyncio.get_running_loop()
        session.brain = brain or FakeBrain()
        session.active = True
        session.executor._query_rows = lambda kind: (
            (self.clients, None) if self.clients is not None
            else ([], "no hyprctl in tests"))
        self.mouth = mouth or Mouth()
        session.feedback._speak_now = self.mouth
        self.speech = asyncio.create_task(session._speech_loop())
        self.addCleanup(self.speech.cancel)
        self.addCleanup(self.mouth.release.set)

        self.ears = Ears(self)
        record = mock.patch.object(
            listen_local, "record_utterance", self.ears)
        transcribe = mock.patch.object(
            listen_local, "transcribe", lambda *a, **k: self.heard)
        record.start()
        transcribe.start()
        self.addCleanup(record.stop)
        self.addCleanup(transcribe.stop)
        return session


class TurnTests(EngineTestCase):
    async def test_a_whole_turn_runs_audio_to_speech(self):
        """The pipeline, end to end: what she heard is what the brain gets."""
        brain = FakeBrain(["Closing it.", "Done."])
        session = self.build(brain)
        brain.spoken_first.set()  # not what this test is about

        await session._turn()

        self.assertEqual(brain.asked, ["close the browser"])
        self.assertEqual(self.mouth.spoken, ["Closing it.", "Done."])

    async def test_sentences_are_spoken_as_they_arrive(self):
        """Not batched at the end -- that is the entire reason for this design.

        Whisper has already spent 1.5s of the latency budget before the brain
        sees the sentence. Holding the reply until the last token arrives puts
        the whole thinking time in front of the first word and hands back
        everything streaming was meant to buy.
        """
        brain = FakeBrain(["Switching workspace.", "Done."])
        session = self.build(brain)

        def mouth(text):
            self.spoken.append(text)
            brain.spoken_first.set()

        self.spoken = []
        session.feedback._speak_now = mouth

        await session._turn()

        self.assertTrue(
            brain.streamed_live,
            "the second sentence was produced before the first was spoken — "
            "the reply is being collected and spoken in one lump at the end")
        self.assertEqual(self.spoken, ["Switching workspace.", "Done."])

    async def test_noise_never_reaches_the_brain(self):
        """whisper answers an empty clip with its best guess at a sentence.

        `clean()` rejects those, and a rejected transcript must stop here: the
        classic is "Thank you." arriving as an instruction someone gave.
        """
        brain = FakeBrain()
        session = self.build(brain)
        self.heard = ""

        await session._turn()

        self.assertEqual(brain.asked, [])
        self.assertEqual(self.mouth.spoken, [local_engine.NOT_CAUGHT])

    async def test_a_silent_room_is_not_a_turn(self):
        """No audio at all is not "say again" -- it is nobody talking."""
        brain = FakeBrain()
        session = self.build(brain)
        self.audio = b""

        await session._turn()

        self.assertEqual(brain.asked, [])
        self.assertEqual(self.mouth.spoken, [])


class MicrophoneGateTests(EngineTestCase):
    """Her voice must not come back in as the next instruction.

    The realtime engine was bitten by exactly this: on speakers, her reply
    reached the microphone, was transcribed as the user, and cancelled her
    mid-word. She answered herself eight times in a row.
    """

    def running(self, session):
        """The real loop, recording turn after turn, cancelled on the way out."""
        self.ears.shut = threading.Event()
        self.addCleanup(self.ears.shut.set)
        self.loop = asyncio.create_task(session._listen_loop())
        self.addCleanup(self.loop.cancel)
        return self.loop

    def why(self):
        """What the fakes and the loop were doing, for a failure message."""
        loop = getattr(self, "loop", None)
        if loop is None:
            state = "not started"
        elif not loop.done():
            state = "running"
        elif loop.cancelled():
            state = "cancelled"
        else:
            state = f"died: {loop.exception()!r}"
        return (f" [captures={self.ears.captures}, "
                f"mouth started={self.mouth.started.is_set()}, "
                f"mouth holding={self.mouth.holding}, listen loop {state}]")

    async def test_the_microphone_stays_shut_while_she_speaks(self):
        """Asserted against the recorder in the running loop, not against a turn.

        The mouth holds the sentence until this test lets go, so the whole
        question is whether the loop reopens the microphone in the meantime.
        The wait below can only be made MORE true by a loaded machine —
        everything the engine would have to do to reopen the recorder gets
        slower under load, never skipped — which is the opposite of the
        earlier version of this test, where load turned a not-yet into a
        failure.
        """
        session = self.build(FakeBrain(["One."]), mouth=Mouth(gated=True))
        loop = self.running(session)

        await self.mouth.wait_until_speaking(self)

        self.assertFalse(
            await asyncio.to_thread(self.ears.again.wait, 1.5),
            "the microphone reopened while she was still speaking — she is "
            "recording her own voice, and the transcript of it is the next "
            "instruction")

        # And it is a gate, not a seizure: it opens again once she stops.
        self.mouth.release.set()
        self.assertTrue(await asyncio.to_thread(self.ears.again.wait, 30),
                        "the microphone never reopened at all after she "
                        "finished — listening is now stuck shut")
        # Stopped before asking what she said: the loop is taking turns for as
        # long as it is alive, and an ungated mouth will keep adding to this.
        loop.cancel()
        self.assertEqual(self.mouth.spoken[0], "One.")

    async def test_barge_in_lets_the_microphone_stay_open_while_she_talks(self):
        """The crude interruption this engine offers, and the only one.

        The mirror image, and the same recorder: with the mouth still holding
        the sentence, a second capture starting is proof the loop moved on
        without waiting for her. Load makes this slower to arrive, not absent.
        """
        session = self.build(FakeBrain(["One."]), mouth=Mouth(gated=True),
                             barge_in=True)
        self.running(session)

        await self.mouth.wait_until_speaking(self)

        self.assertTrue(
            await asyncio.to_thread(self.ears.again.wait, 30),
            "the microphone never reopened while she was speaking — barge_in "
            "is not letting the turn move on")
        # Not a claim about timing: a gated mouth appends only when released,
        # and nothing releases this one until cleanup. If this ever fails, the
        # barrier above has stopped being one and the test proves nothing.
        self.assertEqual(self.mouth.spoken, [],
                         "the mouth was released early — the assertion above "
                         "no longer proves she was still speaking")

    async def test_toggling_off_drops_what_she_has_not_said_yet(self):
        """The second sentence is provably still queued when the toggle lands.

        The mouth is holding the first one, so the speech loop cannot have
        reached the second: what the drain removes is not a matter of timing.
        """
        session = self.build(FakeBrain(), mouth=Mouth(gated=True),
                             barge_in=True)
        await session._say("One.")
        await session._say("Two.")
        await self.mouth.wait_until_speaking(self)

        await session._set_active(False)

        self.assertEqual(session._speech.qsize(), 0)
        self.mouth.release.set()
        await asyncio.sleep(0.05)
        self.assertNotIn("Two.", self.mouth.spoken,
                         "a sentence dropped by the toggle was spoken anyway")


class ScriptedBrain(WarmBrain):
    """The real brain and its real gate, with a scripted model behind them.

    Only `_turn` is replaced, so the SDK and `_with_desktop` never run. Every
    call the "model" makes goes through the real `_pre_tool_use`, and an
    allowed call is recorded, not executed. Text not in the script is a
    release message, and the model makes `release_calls` for it.
    """

    SCRIPT = {
        "commit my notes and reboot": ["git -C ~/notes commit -am wip", "reboot"],
        "what time is it": ["date"],
        "reboot now": ["reboot"],
    }

    def __init__(self, config, executor):
        super().__init__(config, executor)
        self._client = object()  # a session exists; ask_stream would say NO_SESSION
        self.release_calls = [("Bash", {"command": "reboot"})]
        self.allowed = []
        # Set to hold "what time is it" mid-turn; `entered` says it got there.
        self.block: asyncio.Event | None = None
        self.entered = asyncio.Event()

    async def _turn(self, text, *, from_user=True):
        if text == "what time is it" and self.block:
            self.entered.set()
            await self.block.wait()
        if text in self.SCRIPT:
            calls = [("Bash", {"command": c}) for c in self.SCRIPT[text]]
        else:
            calls = self.release_calls
        for tool, tool_input in calls:
            out = await self._pre_tool_use(
                {"tool_name": tool, "tool_input": tool_input}, None, None)
            if out["hookSpecificOutput"]["permissionDecision"] == "allow":
                self.allowed.append(tool_input["command"])
        yield "ok."


class EchoBrain(ScriptedBrain):
    """A scripted model that holds on "hold it" and says `reply` (#86).

    `hold_kind` "brain" holds a Claude Code Bash call; "executor" holds our own
    `omarchy_cli`, parked in `executor.pending`. Anything else it is told goes,
    for the executor kind, to `confirm_last` with the words it heard (or
    `phrase`, a model that claims the user said something) -- what
    `mcp_server.call_tool` does, minus CONFIRM_DELAY. Tool names go in `allowed`.
    """

    def __init__(self, config, executor, hold_kind="brain", command="reboot",
                 reply="ok."):
        super().__init__(config, executor)
        self.hold_kind, self.command, self.reply = hold_kind, command, reply
        self.release_calls = [("Bash", {"command": command})]
        self.phrase = None
        self.turns = []

    async def _turn(self, text, *, from_user=True):
        from omarchy_voice.session import _matches

        self.turns.append(text)
        if text == "hold it":
            calls = [("Bash" if self.hold_kind == "brain"
                      else "mcp__omarchy__omarchy_cli", {"command": self.command})]
        elif self._releasing:
            calls = self.release_calls
        elif self.hold_kind == "executor":
            calls = [("mcp__omarchy__confirm_last", {"phrase": self.phrase or text})]
        else:
            calls = []
        for tool, tool_input in calls:
            out = await self._pre_tool_use(
                {"tool_name": tool, "tool_input": tool_input}, None, None)
            if out["hookSpecificOutput"]["permissionDecision"] != "allow":
                continue
            self.allowed.append(tool)
            if tool == "mcp__omarchy__omarchy_cli":
                self.executor.call("omarchy_cli", tool_input)
            elif tool == "mcp__omarchy__confirm_last" and _matches(
                    tool_input["phrase"], self.config.confirm_words,
                    allow_negation=False):
                self.executor.run_pending()
        if self.reply:
            yield self.reply


class ReleaseTurnTests(EngineTestCase):
    """Confirming a held action runs that action, once, and nothing else (#76).

    It used to replay the last utterance with the held action pre-approved:
    everything else in the sentence ran a second time, and if the user had
    said something else since, that ran instead and the approval stayed armed.
    """

    def build_scripted(self):
        session = self.build()
        # Its own config: build() hard-codes dry_run=True, and a dry run would
        # refuse the very call this is about. The executor is shared, so the
        # transcript and the actions land in one place.
        brain = ScriptedBrain(Config(notify=False, dry_run=False), session.executor)
        session.brain = brain
        return session, brain

    async def confirm(self, session):
        await session._local_confirm()
        await asyncio.gather(*list(session._tasks))

    async def test_confirm_runs_the_held_action_once_not_the_utterance(self):
        """The commit that already ran is not run again when the reboot is confirmed."""
        session, brain = self.build_scripted()

        await session._answer("commit my notes and reboot")
        self.assertEqual(brain.pending, "reboot")
        await self.confirm(session)

        self.assertEqual(brain.allowed, ["git -C ~/notes commit -am wip", "reboot"])

    async def test_confirm_after_another_utterance_runs_only_the_held_action(self):
        """Confirm releases what was held, not what was said last, and the
        approval does not outlive its release turn."""
        session, brain = self.build_scripted()

        await session._answer("commit my notes and reboot")
        await session._answer("what time is it")
        await self.confirm(session)

        self.assertEqual(brain.allowed,
                         ["git -C ~/notes commit -am wip", "date", "reboot"])

        await session._answer("reboot now")
        self.assertEqual(brain.allowed,
                         ["git -C ~/notes commit -am wip", "date", "reboot"],
                         "a later reboot ran on an approval that should be spent")
        self.assertEqual(brain.pending, "reboot")


    async def test_a_cancel_after_confirm_withdraws_the_approval(self):
        """A cancel ends an approval, even one whose release turn has not started.

        Confirming while an ordinary turn runs queues the release turn behind it.
        A cancel in between used to answer "nothing to cancel" -- the hold
        was already gone -- and the queued release turn then ran what was
        cancelled (#76).
        """
        session, brain = self.build_scripted()
        await session._answer("reboot now")
        self.assertEqual(brain.pending, "reboot")

        brain.block = asyncio.Event()
        running = asyncio.create_task(session._answer("what time is it"))
        await brain.entered.wait()              # an ordinary turn holds the lock
        await session._local_confirm()          # the release turn queues behind it
        reply = await session._local_cancel()   # ...and is cancelled before it starts
        brain.block.set()
        await running
        await asyncio.gather(*list(session._tasks))

        self.assertTrue(reply.startswith("Cancelled: reboot"), reply)
        self.assertNotIn("reboot", brain.allowed)
        self.assertIsNone(brain._approved)

    async def test_an_announcement_is_neither_released_nor_replayed(self):
        """A finished watch between the hold and the confirm changes nothing (#74).

        The announcement is an ordinary turn: it cannot spend the held
        action, and confirming afterwards releases the held action, not it.
        """
        from omarchy_voice.realtime import watch_message

        session, brain = self.build_scripted()
        await session._answer("reboot now")
        await session._answer(watch_message({
            "target": "Work:1.2", "label": "pytest", "seconds": 3.0,
            "vanished": False, "timed_out": False, "tail": "3 failed"}),
            from_user=False)
        self.assertEqual(brain.allowed, [], "an announcement ran the held action")
        self.assertEqual(brain.pending, "reboot")

        await self.confirm(session)
        self.assertEqual(brain.allowed, ["reboot"])
        self.assertIsNone(brain._approved)
        self.assertEqual(await session._local_confirm(), "nothing to confirm")


class HoldTests(EngineTestCase):
    async def test_a_held_action_is_spoken_and_confirm_releases_it(self):
        """The gate that stops a misheard sentence rebooting the machine.

        Said out loud from here rather than left to the model: the reply may
        or may not mention it, and "it is waiting for you" is not something to
        leave to phrasing.
        """
        brain = FakeBrain(["I need a yes for that."], hold="omarchy reboot")
        session = self.build(brain)
        brain.spoken_first.set()

        await session._turn()

        self.assertIn("omarchy reboot", " ".join(self.mouth.spoken))
        self.assertIn("cancel", " ".join(self.mouth.spoken).lower())
        self.assertIn("waiting for you", " ".join(self.mouth.spoken).lower())

        reply = await session._local_confirm()

        self.assertIn("omarchy reboot", reply)
        self.assertEqual(brain.confirmed, ["omarchy reboot"])
        self.assertIsNone(brain.pending)
        # The brain's release message is sent, marked as a release -- not the
        # instruction again, which ran the rest of it twice (#76).
        await asyncio.gather(*list(session._tasks))
        self.assertEqual(brain.asked, ["close the browser", "omarchy reboot"])
        self.assertEqual(brain.released, [False, True])

    async def test_confirm_logs_release_not_heard(self):
        """The log must not claim the user said the release message."""
        brain = FakeBrain(["I need a yes for that."], hold="omarchy reboot")
        session = self.build(brain)
        brain.spoken_first.set()
        logged = []
        session.feedback.log = logged.append

        await session._turn()
        await session._local_confirm()
        await asyncio.gather(*list(session._tasks))

        self.assertIn("release omarchy reboot", logged)
        self.assertEqual(len([l for l in logged if l.startswith("heard")]), 1)

    async def test_cancel_drops_the_held_action(self):
        brain = FakeBrain(["I need a yes for that."], hold="omarchy reboot")
        session = self.build(brain)
        brain.spoken_first.set()
        await session._turn()

        reply = await session._local_cancel()

        self.assertIn("Cancelled", reply)
        self.assertIsNone(brain.pending)
        self.assertEqual(await session._local_cancel(), "nothing to cancel")

    async def test_our_own_tools_still_hold_at_the_executor(self):
        """Two gates, one user-facing verb: `listen confirm` releases either."""
        session = self.build(FakeBrain())
        session.executor.call("omarchy_cli", {"command": "reboot"})
        self.assertIsNotNone(session.executor.pending)

        reply = await session._local_confirm()

        self.assertIsNone(session.executor.pending)
        self.assertIn("reboot", reply)


class SpokenConsentTests(EngineTestCase):
    """A spoken "confirm" releases a held action, and her own voice cannot (#86).

    One test per row of the plan's demonstration table: E is her own voice
    coming back in, R is the user. The engine's clock is a stepped one, so no
    row depends on how loaded the machine is: a scaled real clock let the
    at-mic-open rows through under load. Onsets are seconds after her last
    pw-cat returned, the 0.35 s echo tail included, against the default 1.0 s
    guard: "at mic open" is 0.35, "late" 1.35 and R2's "too soon" 0.55.
    """

    AT_OPEN, LATE, EARLY = 0.35, 1.35, 0.55

    def setUp(self):
        super().setUp()
        self.now = 1000.0
        clock = types.SimpleNamespace(monotonic=lambda: self.now)
        # The tail is carried by the onsets above, so nothing sleeps it.
        for patcher in (mock.patch.object(local_engine, "time", clock),
                        mock.patch.object(local_engine, "ECHO_TAIL_SECONDS", 0)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def held(self, hold="brain", reply="ok.", command="reboot", mouth=None,
             **overrides):
        """A session whose model holds `command` on "hold it", logging afresh."""
        session = self.build(mouth=mouth, **overrides)
        brain = EchoBrain(Config(notify=False, dry_run=False), session.executor,
                          hold, command, reply)
        session.brain = brain
        feedback.LOG_FILE.write_text("")
        return session, brain

    async def hold(self, session):
        await session._answer("hold it")
        self.assertIsNotNone(session._held(), "the model did not hold anything")

    async def hear(self, session, text, delay):
        """One spoken turn: `text`, starting `delay` after the mic opened."""
        self.heard = text
        self.ears = OnsetEars(self, delay)
        with mock.patch.object(listen_local, "record_utterance", self.ears):
            await session._turn()

    @staticmethod
    def ran(session):
        """How many held actions were released: CONFIRM is written by both gates."""
        return sum(line.startswith("CONFIRM") for line in session.executor.transcript)

    @staticmethod
    def log():
        return feedback.LOG_FILE.read_text()

    async def echo_refused(self, text, delay, line, why, **kwargs):
        """An echo row: refused out loud, for either kind of hold."""
        for kind in ("brain", "executor"):
            with self.subTest(hold=kind):
                session, brain = self.held(kind, **kwargs)
                await self.hold(session)
                await self.hear(session, text, delay)
                self.assertEqual(self.ran(session), 0,
                                 "her own voice released the held action")
                self.assertIsNotNone(session._held())
                self.assertNotIn(text, brain.turns, "the echo reached the model")
                self.assertEqual(self.mouth.spoken[-1], getattr(local_engine, line))
                self.assertIn(f"consent  refused ({why}) {text!r}", self.log())

    async def released(self, session, brain, text):
        self.assertEqual(self.ran(session), 1)
        self.assertIsNone(session._held())
        self.assertNotIn(text, brain.turns, "the confirm reached the model")
        self.assertIn("confirm spoken release: ", self.log())

    # -- the table --------------------------------------------------------
    async def test_e1_echo_at_mic_open_is_too_soon(self):
        await self.echo_refused("Confirm.", self.AT_OPEN, "SOON", "too soon")

    async def test_e1p_late_confirm_is_taken_residue(self):
        """The residue: a late "Confirm." she never said is taken as the user's."""
        for kind in ("brain", "executor"):
            with self.subTest(hold=kind):
                session, brain = self.held(kind)
                await self.hold(session)
                await self.hear(session, "Confirm.", self.LATE)
                await self.released(session, brain, "Confirm.")

    async def test_e1pp_held_command_names_the_word(self):
        await self.echo_refused("Confirm.", self.LATE, "SELF", "said it",
                                command="git commit -m confirm && reboot")

    async def test_e2_reply_confirm_echo_at_mic_open(self):
        await self.echo_refused("Confirm.", self.AT_OPEN, "SOON", "too soon",
                                reply="Reboot is held. Confirm?")

    async def test_e2p_reply_go_ahead_echo_late(self):
        await self.echo_refused("Go ahead.", self.LATE, "SELF", "said it",
                                reply="Reboot is held. Go ahead?")

    async def test_e3_barge_in_echo_while_playing(self):
        for kind in ("brain", "executor"):
            with self.subTest(hold=kind):
                session, brain = self.held(kind, mouth=Mouth(gated=True),
                                           barge_in=True)
                await self.hold(session)
                await self.mouth.wait_until_speaking(self)
                await self.hear(session, "Confirm.", self.AT_OPEN)
                self.assertEqual(self.ran(session), 0,
                                 "her own voice released the held action")
                self.assertIsNotNone(session._held())
                self.assertNotIn("Confirm.", brain.turns)
                self.mouth.release.set()
                await session._speech.join()
                self.assertEqual(self.mouth.spoken[-1], getattr(local_engine, "BARGE"))
                self.assertIn("consent  refused (barge-in) 'Confirm.'", self.log())

    async def test_e4_cancel_echo_at_mic_open_keeps_hold(self):
        for kind in ("brain", "executor"):
            with self.subTest(hold=kind):
                session, brain = self.held(kind)
                await self.hold(session)
                await self.hear(session, "Cancel.", self.AT_OPEN)
                self.assertIsNotNone(session._held(),
                                     "the echo of her own 'cancel' dropped the hold")
                self.assertNotIn("Cancel.", brain.turns)
                self.assertEqual(self.mouth.spoken[-1], getattr(local_engine, "SOON"))
                self.assertIn("consent  refused (too soon) 'Cancel.'", self.log())

    async def test_r1_user_confirm_after_pause_runs_once(self):
        session, brain = self.held()
        await self.hold(session)

        await self.hear(session, "Confirm.", self.LATE)

        await self.released(session, brain, "Confirm.")
        self.assertEqual(brain.allowed, ["Bash"])
        self.assertIsNone(brain._approved)
        self.assertEqual(self.ears.captures, 1)
        # Awaited, not spawned: the mic stays shut through the release turn.
        self.assertEqual(session._tasks, set())
        self.assertIn("confirm spoken release: reboot", self.log())

    async def test_r2_user_confirm_too_soon_is_asked_again(self):
        session, brain = self.held()
        await self.hold(session)

        await self.hear(session, "Confirm.", self.EARLY)

        self.assertEqual(self.ran(session), 0)
        self.assertEqual(self.mouth.spoken[-1], getattr(local_engine, "SOON"))
        self.assertIn("consent  refused (too soon) 'Confirm.'", self.log())

    async def test_r3_model_said_the_word_forces_the_key(self):
        session, brain = self.held(reply="Please confirm.")
        await self.hold(session)

        await self.hear(session, "Confirm.", self.LATE)

        self.assertEqual(self.ran(session), 0)
        self.assertNotIn("Confirm.", brain.turns)
        self.assertEqual(self.mouth.spoken[-1], getattr(local_engine, "SELF"))
        self.assertIn("consent  refused (said it) 'Confirm.'", self.log())

    async def test_r4_user_cancel_after_pause(self):
        session, brain = self.held()
        await self.hold(session)

        await self.hear(session, "Cancel.", self.LATE)

        self.assertIsNone(session._held())
        self.assertEqual(self.ran(session), 0)
        self.assertNotIn("Cancel.", brain.turns)
        self.assertEqual(self.mouth.spoken[-1], "Cancelled. reboot was not run.")
        self.assertIn("cancel  reboot", self.log())

    async def test_r5_barge_in_user_confirm_refused(self):
        session, brain = self.held(barge_in=True)
        await self.hold(session)
        await session._speech.join()  # she has stopped

        await self.hear(session, "Confirm.", self.LATE)
        await session._speech.join()

        self.assertEqual(self.ran(session), 0)
        self.assertNotIn("Confirm.", brain.turns)
        self.assertEqual(self.mouth.spoken[-1], getattr(local_engine, "BARGE"))
        self.assertIn("consent  refused (barge-in) 'Confirm.'", self.log())

    async def test_barge_in_cancel_still_works(self):
        """D is for confirm only: cancelling is safe."""
        session, brain = self.held(barge_in=True)
        await self.hold(session)
        await session._speech.join()

        await self.hear(session, "Cancel.", self.LATE)
        await session._speech.join()

        self.assertIsNone(session._held())
        self.assertEqual(self.ran(session), 0)
        self.assertEqual(self.mouth.spoken[-1], "Cancelled. reboot was not run.")

    # -- the rest of the spec's verification ------------------------------
    async def test_r1_executor_hold_released_and_outcome_spoken(self):
        session, brain = self.held("executor")
        session.config.dry_run = False
        session.executor._tool_omarchy_cli = lambda **kw: Result(True, "ok")
        await self.hold(session)
        held = session._held()

        await self.hear(session, "Confirm.", self.LATE)

        await self.released(session, brain, "Confirm.")
        self.assertNotIn("mcp__omarchy__confirm_last", brain.allowed)
        self.assertEqual(self.mouth.spoken[-1], "Done.")
        self.assertEqual(brain._notes[-1], f'User said "Confirm." → {held} → ok')

    async def test_dont_confirm_is_not_consumed(self):
        session, brain = self.held()
        await self.hold(session)

        await self.hear(session, "Don't confirm.", self.LATE)

        self.assertIn("Don't confirm.", brain.turns)
        self.assertEqual(self.ran(session), 0)
        self.assertIsNotNone(session._held())

    async def test_typed_confirm_releases_without_timing(self):
        """`listen say confirm` is the user at the keyboard: no A, B' or D."""
        session, brain = self.held(reply="Please confirm.", barge_in=False)
        await self.hold(session)
        self.mouth.release.clear()  # the release turn will be mid-sentence

        self.assertEqual(await session._inject("confirm"), "sent")
        await asyncio.sleep(0.05)
        self.assertTrue(session._tasks, "the socket waited for the release turn")

        self.mouth.release.set()
        await asyncio.gather(*list(session._tasks))
        await self.released(session, brain, "confirm")

    async def test_no_hold_confirm_goes_to_the_model(self):
        session, brain = self.held()

        await self.hear(session, "Confirm.", self.LATE)

        self.assertEqual(brain.turns, ["Confirm."])
        self.assertNotIn("consent", self.log())

    async def test_r1_after_a_silent_hold_turn(self):
        """A terse model's fallback line must not make her own the word."""
        session, brain = self.held(reply=None)
        await self.hold(session)
        self.assertIn("That is waiting for you.", self.mouth.spoken)

        await self.hear(session, "Confirm.", self.LATE)

        await self.released(session, brain, "Confirm.")

    async def test_dropped_speech_does_not_stick_the_clock(self):
        """A sentence drained before the mouth took it must not leave her
        "still talking" forever."""
        session, brain = self.held(barge_in=True)
        await self.hold(session)
        await session._speech.join()
        await session._say("One more thing.")  # queued, the mouth idle
        session._drop_queued_speech()
        session.config.barge_in = False

        await self.hear(session, "Confirm.", self.LATE)

        await self.released(session, brain, "Confirm.")

    async def test_one_breath_wake_confirm_goes_through_consent(self):
        session, brain = self.held(wake_word="oma")
        await self.hold(session)
        session.active = False
        self.heard = "Oma, confirm."
        self.ears = OnsetEars(self, self.LATE)
        with mock.patch.object(listen_local, "record_utterance", self.ears):
            await session._wake_turn()

        self.assertTrue(session.active)
        await self.released(session, brain, "confirm.")

    async def test_the_model_cannot_release_an_executor_hold(self):
        """Only the engine hears consent: a model claiming the user said
        "confirm" is refused at the gate."""
        session, brain = self.held("executor")
        brain.phrase = "confirm"
        await self.hold(session)

        await self.hear(session, "is it done yet", self.LATE)

        self.assertIn("is it done yet", brain.turns)
        self.assertEqual(self.ran(session), 0, "the model released the hold")
        self.assertIsNotNone(session._held())

    async def test_the_screen_names_the_word(self):
        """The spoken prompt no longer names it; the notification does."""
        for barge_in in (False, True):
            with self.subTest(barge_in=barge_in):
                session, brain = self.held(barge_in=barge_in)
                notified = []
                session.feedback.notify = lambda *a, **k: notified.append(a)
                await self.hold(session)
                await session._speech.join()
                body = notified[-1][1]
                self.assertIn("reboot", body)
                self.assertIn("confirm key", body)
                if barge_in:
                    self.assertNotIn('Say "confirm"', body)
                else:
                    self.assertIn('Say "confirm"', body)

    async def test_no_fixed_line_names_a_confirm_phrase(self):
        from omarchy_voice.session import _normalize

        session, brain = self.held(reply=None)
        await self.hold(session)  # the fallback line and the prompt, as spoken
        lines = list(self.mouth.spoken) + [
            local_engine.NOT_CAUGHT, getattr(local_engine, "SOON"),
            getattr(local_engine, "SELF"), getattr(local_engine, "BARGE"),
            getattr(local_engine, "HELD_PROMPT").format(held="x"),
            getattr(local_engine, "HELD_PROMPT_BARGE").format(held="x")]
        for line in lines:
            for phrase in Config().confirm_words:
                self.assertNotIn(_normalize(phrase), _normalize(line), line)


class FailureTests(EngineTestCase):
    async def test_a_brain_failure_mid_turn_is_spoken_not_raised(self):
        """Silence is indistinguishable from a crash from across the room."""
        brain = FakeBrain(["Let me look."], boom=RuntimeError("pipe died"))
        session = self.build(brain)
        brain.spoken_first.set()

        await session._turn()  # must not raise

        self.assertEqual(self.mouth.spoken,
                         ["Let me look.", "Something went wrong with that."])
        # The shared pipe is realigned, or every later turn answers the
        # question before it.
        self.assertEqual(brain.reset_turns, 1)

    async def test_the_daemon_keeps_listening_after_a_bad_turn(self):
        brain = FakeBrain(["Let me look."], boom=RuntimeError("pipe died"))
        session = self.build(brain)
        brain.spoken_first.set()
        await session._turn()

        brain.boom = None
        brain.sentences = ["Done."]
        await session._turn()

        self.assertEqual(len(brain.asked), 2)
        self.assertEqual(self.mouth.spoken[-1], "Done.")

    async def test_no_transcriber_stops_listening_instead_of_recording_into_a_hole(self):
        session = self.build(FakeBrain())
        with mock.patch.object(listen_local, "transcribe",
                               mock.Mock(side_effect=listen_local.Unavailable("no model"))):
            await session._turn()

        self.assertFalse(session.active)
        self.assertTrue(self.mouth.spoken)


class IdleStopTests(EngineTestCase):
    async def test_listening_stops_itself_after_a_long_silence(self):
        """An open microphone is a mode you enter and forget you entered."""
        session = self.build(FakeBrain(), idle_stop_seconds=60)
        self.audio = b""
        session._last_speech -= 600

        await session._turn()

        self.assertFalse(session.active)

    async def test_zero_means_it_stays_open(self):
        session = self.build(FakeBrain(), idle_stop_seconds=0)
        self.audio = b""
        session._last_speech -= 6000

        await session._turn()

        self.assertTrue(session.active)


class WakeWordTests(EngineTestCase):
    async def test_the_wake_word_starts_listening(self):
        session = self.build(FakeBrain(), wake_word="oma")
        session.active = False
        self.heard = "Oma?"

        await session._wake_turn()

        self.assertTrue(session.active)

    async def test_anything_else_heard_while_asleep_is_dropped(self):
        brain = FakeBrain()
        session = self.build(brain, wake_word="oma")
        session.active = False
        self.heard = "close the browser"

        await session._wake_turn()

        self.assertFalse(session.active)
        self.assertEqual(brain.asked, [])


class OneBreathTests(EngineTestCase):
    """ "Oma, close the browser" is an instruction, not two (#72)."""

    async def test_the_instruction_after_the_wake_word_runs(self):
        brain = FakeBrain()
        session = self.build(brain, wake_word="oma")
        session.active = False
        self.heard = "Oma, close the browser."

        await session._wake_turn()

        self.assertTrue(session.active)
        self.assertEqual(brain.asked, ["close the browser."])

    async def test_a_recording_cut_off_by_the_cap_only_wakes(self):
        """Its end may be missing: "close everything except" must not run."""
        brain = FakeBrain()
        session = self.build(brain, wake_word="oma", wake_max_seconds=0)
        session.active = False
        self.heard = "Oma, close the browser."

        await session._wake_turn()

        self.assertTrue(session.active)
        self.assertEqual(brain.asked, [])

    async def test_talking_about_her_only_wakes(self):
        brain = FakeBrain()
        session = self.build(brain, wake_word="oma")
        session.active = False
        self.heard = "I told Oma to close the browser"

        await session._wake_turn()

        self.assertTrue(session.active)
        self.assertEqual(brain.asked, [])


class EndOfSpeechTests(EngineTestCase):
    async def test_a_turn_ends_on_the_local_hold_not_the_realtime_one(self):
        session = self.build(FakeBrain())
        holds = []
        ears = self.ears
        with mock.patch.object(listen_local, "record_utterance",
                               lambda *a, **k: holds.append(a[2]) or ears(*a, **k)):
            await session._turn()
        self.assertEqual(holds, [0.8])
        self.assertEqual(session.config.silence_hold_seconds, 1.5)

    async def test_the_trace_starts_when_the_user_stopped_talking(self):
        session = self.build(FakeBrain(), trace_timings=True)

        await session._turn()

        [line] = [l for l in feedback.LOG_FILE.read_text().splitlines() if "TIMING" in l]
        self.assertIn("endpoint=0.80s", line)
        self.assertIn("transcribe=", line)

class RouterTests(EngineTestCase):
    """A fixed command runs without the model, through the same gate (#71)."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.clients = FIXTURE_CLIENTS

    async def test_a_routed_command_never_reaches_the_brain(self):
        brain = FakeBrain()
        session = self.build(brain)

        await session._answer("switch to workspace one")

        self.assertEqual(brain.asked, [])
        self.assertEqual(self.mouth.spoken,
                         ["[dry-run] would run: dispatch focus workspace='1'"])
        [note] = brain.notes
        self.assertTrue(note.endswith("→ dry-run"), note)

    async def test_a_command_that_ran_says_what_it_did(self):
        session = self.build()
        session.config.dry_run = False
        # On the handler, not _shell: rendering needs the Hyprland Lua stub,
        # which a test machine may not have.
        session.executor._tool_hypr_dispatch = lambda **kw: Result(True, "ok")

        await session._answer("switch to workspace one")

        self.assertEqual(self.mouth.spoken, ["Workspace one."])

    async def test_a_miss_goes_to_the_brain(self):
        brain = FakeBrain()
        session = self.build(brain)
        brain.spoken_first.set()

        await session._answer("what is the weather for today?")

        self.assertEqual(brain.asked, ["what is the weather for today?"])

    async def test_nothing_is_routed_while_something_waits_for_a_yes(self):
        brain = FakeBrain()
        session = self.build(brain)
        brain.pending = "x"

        await session._answer("switch to workspace one")

        self.assertEqual(brain.asked, ["switch to workspace one"])

    async def test_router_off_sends_everything_to_the_brain(self):
        brain = FakeBrain()
        session = self.build(brain, router=False)

        await session._answer("switch to workspace one")

        self.assertEqual(brain.asked, ["switch to workspace one"])

    async def test_a_held_route_speaks_the_gate_not_the_model_instruction(self):
        brain = FakeBrain()
        session = self.build(brain, confirm_patterns=[r"window\.close"])

        await session._answer("close the weather window")

        self.assertIsNotNone(session.executor.pending)
        spoken = " ".join(self.mouth.spoken)
        self.assertIn("waiting for you", spoken)
        self.assertNotIn("Stop here", spoken)
        self.assertTrue(brain.notes[-1].endswith("→ held for confirmation"))

    async def test_a_denied_route_speaks_the_refusal_only(self):
        session = self.build(deny_patterns=[r"window\.close"])

        await session._answer("close the weather window")

        [line] = self.mouth.spoken
        self.assertTrue(line.startswith("refused"), line)
        self.assertNotIn("Tell the user", line)
        self.assertIsNone(session.executor.pending)

    async def test_a_typed_turn_is_routed_too(self):
        brain = FakeBrain()
        session = self.build(brain)

        await session._inject("what windows are open")
        await asyncio.gather(*session._tasks)

        self.assertIn("Discord", " ".join(self.mouth.spoken))
        self.assertEqual(brain.asked, [])

    async def test_a_release_turn_is_never_routed(self):
        """A release turn carries the approval to the brain, unchanged (#76)."""
        brain = FakeBrain()
        session = self.build(brain)
        brain.spoken_first.set()

        await session._answer("switch to workspace one", release="x")

        self.assertEqual(brain.asked, ["switch to workspace one"])
        self.assertEqual(brain.released, [True])
        self.assertEqual(brain.notes, [])

    async def test_an_announcement_is_never_routed(self):
        """A finished watch is not something the user said, so the router
        never sees it, whatever it happens to contain (#74)."""
        brain = FakeBrain()
        session = self.build(brain)
        brain.spoken_first.set()

        await session._answer("switch to workspace one", from_user=False)

        self.assertEqual(brain.asked, ["switch to workspace one"])
        self.assertEqual(brain.from_user, [False])
        self.assertEqual(brain.notes, [])
        self.assertNotIn("routed", feedback.LOG_FILE.read_text())

    async def test_a_routed_turn_is_timed(self):
        session = self.build(trace_timings=True)

        await session._answer("switch to workspace one")

        self.assertTrue([l for l in feedback.LOG_FILE.read_text().splitlines()
                         if "TIMING" in l])


class ControlTests(EngineTestCase):
    async def test_every_verb_the_realtime_engine_answered_is_answered(self):
        session = self.build(FakeBrain())
        session.active = False
        for verb, expected in (("start", "listening"), ("toggle", "idle"),
                               ("stop", "idle"), ("confirm", "nothing to confirm"),
                               ("cancel", "nothing to cancel"),
                               ("say hello there", "sent"), ("quit", "stopping")):
            with self.subTest(verb=verb):
                self.assertEqual(
                    await asyncio.to_thread(session._control, verb), expected)
        self.assertEqual(session._control("wiggle"), "unknown command 'wiggle'")

    async def test_a_typed_turn_does_not_hold_the_socket_open(self):
        """`listen say` is a keybinding, not a caller with time to wait."""
        brain = FakeBrain(["Done."])
        session = self.build(brain)
        brain.spoken_first.set()

        self.assertEqual(await session._inject("  what time is it  "), "sent")
        await asyncio.sleep(0.05)

        self.assertEqual(brain.asked, ["what time is it"])
        self.assertEqual(await session._inject("   "), "nothing to say")


class WatchAnnounceTests(EngineTestCase):
    """A watched command is announced on this engine too (#74).

    `watch_terminal` promised "I will say when it finishes", and only the
    realtime engine polled for it. Here nothing did, so the promise was a lie.
    """

    def finishing(self, session, label="pytest"):
        """A watch on Work:1.2 whose pane has gone back to the shell."""
        session.executor._tmux_panes = lambda: [
            {"target": "Work:1.2", "idle": True, "command": "bash"}]
        session.executor._capture_pane = lambda target, lines=0: Result(True, "3 failed")
        session.executor.watch("Work:1.2", label, seen_busy=True)

    @staticmethod
    def quiet(*a, **k):
        threading.Event().wait(0.01)  # a quiet room: the loop turns over
        return b""

    def session(self, brain=None, **overrides):
        """Listening in a quiet room, with the watcher polling fast and the
        notifications recorded."""
        self.brain = brain or FakeBrain(["pytest failed."])
        self.brain.spoken_first.set()
        # idle_stop 0: a quiet room must not mute it.
        session = self.build(self.brain, idle_stop_seconds=0, **overrides)
        self.notified = []
        session.feedback.notify = lambda *a, **k: self.notified.append(a)
        for patcher in (mock.patch.object(listen_local, "record_utterance", self.quiet),
                        mock.patch.object(local_engine, "WATCH_POLL_SECONDS", 0.01)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return session

    def start(self, session, *loops):
        for loop in loops or (session._watch_loop, session._listen_loop):
            task = asyncio.create_task(loop())
            self.addCleanup(task.cancel)

    async def test_a_finished_watch_is_announced(self):
        """Driven through the real run(): the daemon, not a method on it."""
        brain = FakeBrain(["pytest failed."])
        brain.spoken_first.set()
        session = self.build(brain, idle_stop_seconds=0)  # a quiet room must not mute it
        self.finishing(session)

        stub = mock.Mock()
        with mock.patch.object(local_engine, "brain_for", return_value=brain), \
                mock.patch.object(local_engine, "ControlServer", return_value=stub), \
                mock.patch.object(listen_local.Server, "start", return_value=None), \
                mock.patch.object(listen_local, "record_utterance", self.quiet), \
                mock.patch.object(local_engine, "WATCH_POLL_SECONDS", 0.01, create=True):
            running = asyncio.create_task(session.run())
            try:
                announced = await self.until(lambda: "pytest failed." in self.mouth.spoken)
            finally:
                session._stop.set()
                await asyncio.wait_for(running, 5)

        self.assertTrue(announced, "the finished watch was never announced")
        [message] = brain.asked
        self.assertIn("pytest finished", message)
        self.assertIn("3 failed", message)
        self.assertEqual(session.executor._watches, {})

    async def test_listening_announces_once(self):
        session = self.session()
        self.finishing(session)
        self.start(session)

        self.assertTrue(await self.until(lambda: self.mouth.spoken))
        await asyncio.sleep(0.2)  # twenty more polls

        [message] = self.brain.asked
        self.assertIn("pytest finished in 0 seconds.", message)
        self.assertIn("3 failed", message)
        self.assertEqual(self.brain.from_user, [False])
        self.assertEqual(self.brain.released, [False])
        self.assertEqual(self.mouth.spoken, ["pytest failed."])
        self.assertNotIn("heard", feedback.LOG_FILE.read_text())

    async def muted(self, **overrides):
        session = self.session(**overrides)
        session.active = False
        session._wake_ready = bool(overrides.get("wake_word"))
        self.finishing(session)
        self.start(session)

        self.assertTrue(await self.until(lambda: self.notified))
        await asyncio.sleep(0.1)
        self.assertEqual(self.notified, [("Oma", "pytest finished in 0 seconds.")])
        self.assertEqual(self.mouth.spoken, [])
        self.assertEqual(self.brain.asked, [])
        self.assertEqual(session._announcements, [])

    async def test_muted_notifies_and_says_nothing(self):
        await self.muted()

    async def test_muted_with_the_wake_word_notifies_and_says_nothing(self):
        await self.muted(wake_word="oma")

    async def test_queued_then_muted_becomes_a_notification(self):
        session = self.session()
        self.finishing(session)
        [job] = session.executor.poll_watches()
        session._announcements.append(job)

        await session._set_active(False)

        self.assertIn(("Oma", "pytest finished in 0 seconds."), self.notified)
        self.assertEqual([n for n in self.notified if n[0] == "Oma"],
                         [("Oma", "pytest finished in 0 seconds.")])
        self.assertEqual(session._announcements, [])
        self.assertEqual(self.mouth.spoken, [])
        self.assertEqual(self.brain.asked, [])

    async def test_a_job_finishing_mid_capture_waits_for_the_capture(self):
        """The watcher never speaks into an open microphone; the loop does, between captures."""
        session = self.session()
        opened, done = threading.Event(), threading.Event()

        def capture(*a, **k):
            if not done.is_set():
                opened.set()
                done.wait(30)
                return b""
            return self.quiet()

        patcher = mock.patch.object(listen_local, "record_utterance", capture)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(done.set)
        mic = []
        mouth = self.mouth
        session.feedback._speak_now = lambda text: (
            mic.append(session.feedback.mic_open), mouth(text))

        self.start(session, session._listen_loop)
        self.assertTrue(await asyncio.to_thread(opened.wait, 5))
        self.finishing(session)
        self.start(session, session._watch_loop)
        self.assertTrue(await self.until(lambda: not session.executor._watches))
        await asyncio.sleep(0.2)

        self.assertEqual(self.brain.asked, [], "announced into an open capture")
        self.assertEqual(self.mouth.spoken, [])

        done.set()
        self.assertTrue(await self.until(lambda: self.mouth.spoken))
        self.assertEqual(self.mouth.spoken, ["pytest failed."])
        self.assertEqual(mic, [False])

    async def test_a_watcher_that_raises_keeps_polling(self):
        session = self.session()
        self.finishing(session)
        real, calls = session.executor.poll_watches, []

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("tmux went away")
            return real()

        session.executor.poll_watches = flaky
        self.start(session)

        self.assertTrue(await self.until(lambda: self.mouth.spoken))
        self.assertIn("warn    watcher: RuntimeError: tmux went away",
                      feedback.LOG_FILE.read_text())
        self.assertEqual(len(self.brain.asked), 1)

    async def test_the_daemons_announce_watches(self):
        from omarchy_voice import realtime
        from omarchy_voice.tools import Executor

        self.assertTrue(local_engine.LocalSession(Config()).executor.announces_watches)
        self.assertTrue(realtime.RealtimeSession(Config()).executor.announces_watches)
        self.assertFalse(Executor(Config()).announces_watches)


class WiringTests(unittest.TestCase):
    """Which engine `omarchy-voice run` actually starts."""

    def test_local_is_the_default(self):
        self.assertEqual(Config().realtime_engine, "local")

    def test_openai_is_still_reachable(self):
        with mock.patch.object(cli.realtime_mod, "run", return_value=7) as old:
            self.assertEqual(cli.cmd_run(None, Config(realtime_engine="openai")), 7)
        old.assert_called_once()

    def test_anything_unrecognised_runs_the_local_chain(self):
        """A typo must not silently fall back to streaming the room to an API."""
        for engine in ("local", "", "locl"):
            with self.subTest(engine=engine), \
                    mock.patch.object(local_engine, "run", return_value=3) as new:
                self.assertEqual(cli.cmd_run(None, Config(realtime_engine=engine)), 3)
                new.assert_called_once()


class PersonaTests(unittest.TestCase):
    """Why a turn that calls a tool used to be five seconds of silence.

    The shared persona says act first and narrate after, which is right when
    the model speaks *while* the tool runs. This pipeline can only say what has
    already been written, so that rule turned every tool call into dead air:
    8.1-9.0s to the first spoken sentence against 2.6s for a turn that just
    answered. With the line below it is 1.4-1.8s either way.
    """

    def _options(self):
        from omarchy_voice import claude_backend
        from omarchy_voice.tools import Executor

        stub = types.SimpleNamespace(system_prompt="SHARED PERSONA")
        with mock.patch.object(claude_backend.WarmBrain, "_options",
                               lambda self: stub):
            config = Config()
            return local_engine.brain_for(config, Executor(config))._options()

    def test_she_is_told_to_say_something_before_calling_a_tool(self):
        self.assertIn("before ANY tool call", self._options().system_prompt)

    def test_it_is_appended_to_the_shared_persona_not_instead_of_it(self):
        """Last word on a disagreement, but nothing of the original dropped."""
        prompt = self._options().system_prompt
        self.assertTrue(prompt.startswith("SHARED PERSONA"))
        self.assertTrue(prompt.rstrip().endswith(local_engine.LOCAL_PERSONA.rstrip()))

    def test_the_announcement_may_not_claim_the_action_happened(self):
        """The honest half of "act first, narrate after" is still load-bearing.

        Announcing a workspace switch is fine; saying it switched before the
        call is a lie the user catches the moment it fails.
        """
        self.assertIn("must never say the action happened", local_engine.LOCAL_PERSONA)


class ReadinessTests(unittest.TestCase):
    def test_a_chain_with_no_mouth_is_not_ready(self):
        """A daemon that can hear and think but not speak is a light on a mic."""
        with mock.patch.object(local_engine.feedback_mod, "piper_model",
                               return_value=None), \
                mock.patch.object(local_engine.shutil, "which", return_value=None):
            self.assertEqual(local_engine.voice_chain(Config()), "")
            self.assertTrue(any("no voice" in p
                                for p in local_engine.check_ready(Config())))

    def test_the_cloud_voice_names_its_local_fallback(self):
        config = Config(elevenlabs_enabled=True, elevenlabs_voice_id="v1")
        with mock.patch.object(local_engine.elevenlabs, "ready", return_value=True), \
                mock.patch.object(local_engine.feedback_mod, "piper_model",
                                  return_value=Path("/voice.onnx")), \
                mock.patch.object(local_engine.shutil, "which", return_value="/bin/piper"):
            self.assertIn("piper if it fails", local_engine.voice_chain(config))


class RefusalLogTests(unittest.TestCase):
    def test_the_daemon_logs_refusals_through_its_executor(self):
        """#13: the brain is built on this Executor, so its refusals use this sink."""
        session = local_engine.LocalSession(Config(notify=False, dry_run=True))
        self.assertEqual(session.executor.on_record, session.feedback.log)

if __name__ == "__main__":
    unittest.main()


class NoBackendNotice(unittest.TestCase):
    """#18: a daemon that cannot start says so on the desktop, not only on the bar.

    The bar mark said "unconfigured" and nothing else did. On a fresh install
    that is a small grey glyph nobody is looking at, so the answer to "why does
    voice do nothing" was a journal.
    """

    def notices(self, module, problems, config):
        seen = []
        with mock.patch.object(module, "check_ready", return_value=problems), \
                mock.patch.object(feedback.Feedback, "notify",
                                  lambda self, title, body="", urgency="low": seen.append(body)), \
                mock.patch.object(feedback.Feedback, "state", lambda *a, **k: None):
            code = module.run(config)
        return code, seen

    def test_the_local_engine_says_it_once_and_exits_zero(self):
        code, seen = self.notices(local_engine, ["claude code is not installed"], Config())
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 1, seen)
        self.assertIn("doctor", seen[0])

    def test_the_realtime_engine_says_the_same_thing(self):
        from omarchy_voice import realtime
        config = Config(realtime_engine="openai")
        code, seen = self.notices(realtime, [f"{config.api_key_env} is not set"], config)
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 1, seen)
        self.assertIn("doctor", seen[0])

    def test_a_ready_machine_is_not_told_anything(self):
        with mock.patch.object(local_engine, "check_ready", return_value=[]), \
                mock.patch.object(local_engine, "_run_until_done", return_value=0), \
                mock.patch.object(feedback.Feedback, "notify",
                                  lambda *a, **k: self.fail("notified a working install")):
            self.assertEqual(local_engine.run(Config()), 0)
