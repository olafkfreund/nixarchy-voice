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

from omarchy_voice import cli, feedback, listen_local, local_engine
from omarchy_voice.config import Config


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
        self.confirmed = []
        self.reset_turns = 0
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

    async def ask_stream(self, text):
        self.asked.append(text)
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

    def build(self, brain=None, mouth=None, **overrides):
        config = Config(notify=False, dry_run=True, **overrides)
        session = local_engine.LocalSession(config)
        session.loop = asyncio.get_running_loop()
        session.brain = brain or FakeBrain()
        session.active = True
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
        self.assertEqual(self.mouth.spoken, [])

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
        # The instruction is replayed, because a Claude Code tool call has no
        # re-executable handle on this side.
        await asyncio.sleep(0)
        await asyncio.sleep(0.05)
        self.assertEqual(brain.asked, ["close the browser"] * 2)

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


if __name__ == "__main__":
    unittest.main()
