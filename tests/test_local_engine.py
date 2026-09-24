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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
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
        if not gated:
            self.release.set()

    def __call__(self, text):
        self.started.set()
        # Bounded only so a genuine deadlock ends as a failing test rather than
        # a hung suite. A timeout here speaks nothing: it is not a sentence.
        if self.release.wait(30):
            self.spoken.append(text)

    async def wait_until_speaking(self, case):
        """Block until she is provably mid-sentence."""
        case.assertTrue(await asyncio.to_thread(self.started.wait, 30),
                        "the sentence was never handed to the mouth at all")


class Ears:
    """The fake microphone. Counts captures, and says when one starts again.

    `again` is the whole of the gate test: the microphone reopening is an
    event, so both "it did" and "it did not yet" are questions asked of an
    event rather than of a clock reading taken at an arbitrary instant.
    """

    def __init__(self, case):
        self.case = case
        self.captures = 0
        self.again = threading.Event()

    def __call__(self, *args, **kwargs):
        self.captures += 1
        if self.captures > 1:
            self.again.set()
        return self.case.audio


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
        loop = asyncio.create_task(session._listen_loop())
        self.addCleanup(loop.cancel)
        return loop

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
        self.assertIn("confirm", " ".join(self.mouth.spoken).lower())

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
        self.assertIn("needs confirming", spoken)
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

    async def until(self, predicate, seconds=2.0):
        deadline = asyncio.get_running_loop().time() + seconds
        while not predicate() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        return predicate()

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
