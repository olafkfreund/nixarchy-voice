"""The realtime engine's own safety-critical parts.

Two things are worth testing without a websocket or a microphone: that the
tool schemas convert to the shape the Realtime API wants, and that the
spoken-confirmation gate cannot be talked through. In realtime the user's
"yes, do it" goes to the model as audio and never reaches this process, and
her own "Confirm?" can come back as one, so only the confirm key releases a
hold (#113).

Run with: python3 -m unittest discover -s tests
"""

import array
import asyncio
import base64
import json
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _isolated  # noqa: F401  -- before any omarchy_voice import (#99)

from omarchy_voice import feedback, realtime
from omarchy_voice.config import Config
from omarchy_voice.tools import TOOL_SCHEMAS


class FakeSocket:
    """Records what would have gone over the wire."""

    def __init__(self):
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    def events(self, kind):
        return [e for e in self.sent if e.get("type") == kind]


class ToolConversionTests(unittest.TestCase):
    def setUp(self):
        self.tools = realtime.to_realtime_tools()
        self.by_name = {t["name"]: t for t in self.tools}

    def test_every_executor_tool_survives(self):
        for schema in TOOL_SCHEMAS:
            with self.subTest(tool=schema["name"]):
                converted = self.by_name[schema["name"]]
                self.assertEqual(converted["type"], "function")
                self.assertEqual(converted["parameters"], schema["input_schema"])
                self.assertEqual(converted["description"], schema["description"])

    def test_internal_schema_keys_are_dropped(self):
        for tool in self.tools:
            with self.subTest(tool=tool["name"]):
                self.assertNotIn("input_schema", tool)
                self.assertNotIn("strict", tool)
                self.assertEqual(set(tool), {"type", "name", "description", "parameters"})

    def test_gate_tools_are_realtime_only(self):
        self.assertIn("confirm_last", self.by_name)
        self.assertIn("cancel_last", self.by_name)
        self.assertNotIn("confirm_last", {s["name"] for s in TOOL_SCHEMAS})
        self.assertNotIn("cancel_last", {s["name"] for s in TOOL_SCHEMAS})

    def test_json_serialisable(self):
        json.dumps(self.tools)  # must not raise


class RealtimeSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for name, value in (("LOG_FILE", root / "session.log"),
                            ("STATE_FILE", root / "state.json"),
                            ("STATE_DIR", root),
                            ("RUNTIME_DIR", root)):
            patcher = mock.patch.object(feedback, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

        self.config = Config(dry_run=True, notify=False)
        self.session = realtime.RealtimeSession(self.config)
        self.socket = FakeSocket()
        self.session.ws = self.socket

    def test_the_executor_logs_refusals_to_the_session_log(self):
        """#13: the realtime session had no log line for the policy saying no."""
        self.assertEqual(self.session.executor.on_record, self.session.feedback.log)

    async def test_a_refused_and_a_held_call_reach_the_log_file(self):
        """End to end, through the real session and its real log file.

        The measurement that found #13: a denied `rm -rf` and a held action
        used to leave no line, while the call that ran did.
        """
        log = Path(self.tmp.name) / "session.log"
        await self.session._dispatch("run_shell", {"command": "sudo rm -rf /"})
        await self.session._dispatch("omarchy_cli", {"command": "reboot"})
        await self.session._dispatch("launch_app", {"app": "firefox"})
        text = log.read_text()
        self.assertIn("DENIED  sudo rm -rf /", text)
        self.assertIn("HOLD    omarchy reboot", text)
        self.assertIn("action  launch firefox", text)

    def hold_a_reboot(self):
        """Put a confirm-gated action into the pending slot, the real way."""
        result = self.session.executor.call("omarchy_cli", {"command": "reboot"})
        self.assertFalse(result.ok)
        self.assertIsNotNone(self.session.executor.pending)

    async def test_no_spoken_phrase_releases_a_hold(self):
        """#113: on realtime only the key releases; a reported phrase never does."""
        self.hold_a_reboot()
        for phrase in ["confirm", "yes do it please", "go ahead", "don't confirm", "ok", ""]:
            with self.subTest(phrase=phrase):
                output = await self.session._dispatch("confirm_last",
                                                      {"heard_phrase": phrase})
                self.assertTrue(output.startswith("ERROR:"), output)
                self.assertIn("confirm key", output)
                self.assertIsNotNone(self.session.executor.pending)

    async def test_confirming_nothing_is_an_error(self):
        output = await self.session._dispatch("confirm_last", {"heard_phrase": "confirm"})
        self.assertTrue(output.startswith("ERROR:"), output)

    async def test_cancel_drops_the_held_action(self):
        self.hold_a_reboot()
        output = await self.session._dispatch("cancel_last", {})
        self.assertIn("Cancelled", output)
        self.assertIsNone(self.session.executor.pending)
        output = await self.session._dispatch("confirm_last", {"heard_phrase": "confirm"})
        self.assertTrue(output.startswith("ERROR:"), output)

    async def test_denied_actions_are_still_denied(self):
        output = await self.session._dispatch("run_shell", {"command": "sudo rm -rf /"})
        self.assertTrue(output.startswith("ERROR:"), output)
        self.assertIsNone(self.session.executor.pending)

    async def test_function_call_is_answered_and_a_reply_requested(self):
        await self.session._on_response_done({
            "type": "response.done",
            "response": {"status": "completed", "output": [
                {"type": "function_call", "name": "hypr_dispatch",
                 "call_id": "call_abc",
                 "arguments": json.dumps({"dispatcher": "focus", "args": {"workspace": "3"}})},
            ]},
        })
        outputs = self.socket.events("conversation.item.create")
        self.assertEqual(len(outputs), 1)
        item = outputs[0]["item"]
        self.assertEqual(item["type"], "function_call_output")
        self.assertEqual(item["call_id"], "call_abc")
        self.assertIn("dry-run", item["output"])
        self.assertIsInstance(item["output"], str)
        self.assertEqual(len(self.socket.events("response.create")), 1)

    async def test_same_batch_confirm_last_does_not_release_the_gate(self):
        await self.session._on_response_done({
            "type": "response.done",
            "response": {"status": "completed", "output": [
                {"type": "function_call", "name": "omarchy_cli",
                 "call_id": "call_hold",
                 "arguments": json.dumps({"command": "reboot"})},
                {"type": "function_call", "name": "confirm_last",
                 "call_id": "call_yes",
                 "arguments": json.dumps({"heard_phrase": "confirm"})},
            ]},
        })
        self.assertIsNotNone(self.session.executor.pending)
        outputs = [e["item"]["output"] for e in self.socket.events("conversation.item.create")]
        self.assertTrue(any("confirm key" in o for o in outputs), outputs)

    async def respond(self, name, args, call_id="call_x"):
        """One model response carrying a single function call."""
        await self.session._on_response_done({
            "type": "response.done",
            "response": {"status": "completed", "output": [
                {"type": "function_call", "name": name, "call_id": call_id,
                 "arguments": json.dumps(args)},
            ]},
        })

    async def hold_then_echo(self):
        """#113: hold a reboot, then her own question comes back as a VAD turn."""
        await self.respond("omarchy_cli", {"command": "reboot"}, "call_hold")
        self.assertIsNotNone(self.session.executor.pending)
        await self.session._on_event({"type": "response.output_audio_transcript.done",
                                      "transcript": "Reboot is held. Confirm?"})
        await self.session._on_event({"type": "input_audio_buffer.speech_started"})

    async def test_her_echo_cannot_release_a_hold(self):
        await self.hold_then_echo()
        await self.respond("confirm_last", {"heard_phrase": "Confirm?"}, "call_yes")
        self.assertIsNotNone(self.session.executor.pending)
        last = self.socket.events("conversation.item.create")[-1]["item"]
        self.assertEqual(last["type"], "function_call_output")
        self.assertIn("confirm key", last["output"])

    async def test_the_key_releases_exactly_once_after_an_echo(self):
        await self.hold_then_echo()
        output = await self.session._local_confirm()
        self.assertNotIn("ERROR:", output)
        self.assertIn("[dry-run]", output)
        self.assertIsNone(self.session.executor.pending)
        self.assertEqual(
            "\n".join(self.session.executor.transcript).count("CONFIRM omarchy reboot"), 1)
        self.assertEqual(await self.session._local_confirm(), "nothing to confirm")

    async def test_cancel_still_drops_a_hold_after_an_echo(self):
        await self.hold_then_echo()
        await self.respond("cancel_last", {}, "call_no")
        last = self.socket.events("conversation.item.create")[-1]["item"]
        self.assertIn("Cancelled", last["output"])
        self.assertIsNone(self.session.executor.pending)

    async def test_the_hold_reply_asks_for_the_key(self):
        output = await self.session._dispatch("omarchy_cli", {"command": "reboot"})
        self.assertIn("confirm key", output)
        self.assertNotIn("out loud", output)

    def test_persona_does_not_invite_a_spoken_confirm(self):
        persona = realtime.REALTIME_PERSONA
        block = persona[persona.index("# Confirmations"):]
        self.assertIn("confirm key", block)
        self.assertNotIn("heard_phrase", block)
        self.assertNotIn("call `confirm_last`", block)

    def test_waiting_notification_names_the_key(self):
        self.hold_a_reboot()
        with mock.patch.object(self.session.feedback, "notify") as notify:
            self.session._settle()
        title, body = notify.call_args.args[:2]
        self.assertEqual(title, "Waiting for confirmation")
        self.assertTrue(body.endswith("Press the confirm key."), body)

    async def test_cancelled_response_does_not_dispatch_tools(self):
        await self.session._on_response_done({
            "type": "response.done",
            "response": {"status": "cancelled", "output": [
                {"type": "function_call", "name": "hypr_dispatch",
                 "call_id": "call_int",
                 "arguments": json.dumps({"dispatcher": "focus", "args": {"workspace": "9"}})},
            ]},
        })
        self.assertEqual(self.socket.events("conversation.item.create"), [])
        self.assertEqual(self.session.executor.transcript, [])

    async def test_unparseable_arguments_report_back_instead_of_crashing(self):
        await self.session._on_response_done({
            "type": "response.done",
            "response": {"status": "completed", "output": [
                {"type": "function_call", "name": "hypr_dispatch",
                 "call_id": "call_bad", "arguments": "{not json"},
            ]},
        })
        item = self.socket.events("conversation.item.create")[0]["item"]
        self.assertTrue(item["output"].startswith("ERROR:"), item["output"])

    async def test_a_plain_spoken_reply_sends_nothing_back(self):
        await self.session._on_response_done({
            "type": "response.done",
            "response": {"status": "completed",
                         "output": [{"type": "message", "role": "assistant"}]},
        })
        self.assertEqual(self.socket.sent, [])

    async def test_muting_stops_capture_rather_than_ignoring_it(self):
        await self.session._set_active(True)
        self.assertTrue(self.session._active_event.is_set())
        await self.session._set_active(False)
        self.assertFalse(self.session._active_event.is_set())
        self.assertFalse(self.session.active)

    async def test_manual_vad_commits_on_mute(self):
        self.session.config.realtime_turn_detection = "none"
        self.session._appended_audio = True
        await self.session._set_active(False)
        kinds = [e.get("type") for e in self.socket.sent]
        self.assertIn("input_audio_buffer.commit", kinds)
        self.assertIn("response.create", kinds)

    async def test_barge_in_cancels_and_drops_old_audio(self):
        self.session._audio_item_id = "item_1"
        self.session._audio_response_id = "resp_old"
        self.session._audio_bytes = 4800 * 4
        await self.session._on_event({"type": "input_audio_buffer.speech_started"})
        kinds = [e.get("type") for e in self.socket.sent]
        self.assertIn("response.cancel", kinds)
        self.assertIn("conversation.item.truncate", kinds)
        await self.session._on_event({
            "type": "response.output_audio.delta",
            "response_id": "resp_old",
            "delta": "AAAA",
        })
        # Ignored leftover delta must not have been written as a new speaker.
        self.assertIsNone(self.session.speaker._proc)

    async def test_barge_in_with_nothing_running_sends_no_cancel(self):
        """Speech between turns must not ask to cancel a response that is not there.

        A live session logged "response_cancel_not_active: no active response
        found" on every toggle-on, because barge-in fired response.cancel
        unconditionally.
        """
        self.session._audio_item_id = None
        self.session._audio_response_id = None
        self.session._response_running = False
        await self.session._on_event({"type": "input_audio_buffer.speech_started"})
        kinds = [e.get("type") for e in self.socket.sent]
        self.assertNotIn("response.cancel", kinds)
        self.assertNotIn("conversation.item.truncate", kinds)

    async def test_audio_bytes_reset_between_items(self):
        """Truncation is an offset into one item, so bytes cannot accumulate.

        Carrying a running total across items asked the server to cut past the
        end of what it held: "Audio content of 1850ms is already shorter than
        4850ms".
        """
        for item in ("item_1", "item_1", "item_2"):
            await self.session._on_event({
                "type": "response.output_audio.delta",
                "response_id": "resp_1", "item_id": item, "delta": "AAAA",
            })
        self.assertEqual(self.session._audio_item_id, "item_2")
        self.assertEqual(self.session._audio_bytes, 3)

    async def test_tool_loop_stops_at_max_turns(self):
        """The model must not be able to drive itself indefinitely.

        Each tool round ends by asking for another response, so a model that
        keeps retrying a failing call loops forever. A live session opened
        terminals every ~30 s, minutes after listening was switched off.
        """
        self.session.config.max_turns = 3
        self.session._tool_rounds = 0
        for _ in range(6):
            await self.session._on_event({
                "type": "response.done",
                "response": {"status": "completed", "output": [
                    {"type": "function_call", "name": "hypr_query",
                     "call_id": "c1", "arguments": '{"kind": "workspaces"}'},
                ]},
            })
        self.assertEqual(len(self.socket.events("response.create")), 2)

    async def test_a_new_user_turn_refills_the_budget(self):
        self.session.config.max_turns = 3
        self.session._tool_rounds = 3
        await self.session._on_event({"type": "input_audio_buffer.speech_started"})
        self.assertEqual(self.session._tool_rounds, 0)

    async def test_playback_never_blocks_the_caller(self):
        """Audio must not stall the loop that reads the websocket.

        pw-cat consumes at real time while the model sends far faster, so
        draining its pipe inline blocked event handling — tool calls included —
        for the length of the spoken reply. That was the "hangs after I talk to
        it" symptom.
        """
        from omarchy_voice.realtime import Speaker
        speaker = Speaker(24000)
        chunk = b"\x00\x01" * 2400          # 0.2 s of PCM16
        started = time.monotonic()
        for _ in range(25):                  # 5 s of audio
            await speaker.write(chunk)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(speaker._queue.qsize(), 25)
        await speaker.interrupt()
        self.assertEqual(speaker._queue.qsize(), 0)
        self.assertIsNone(speaker._proc)

    async def test_typed_turn_is_injected_as_a_user_message(self):
        self.assertEqual(await self.session._inject("switch to workspace four"), "sent")
        # A desktop snapshot is sent ahead of it, so pick the user turn out
        # rather than assuming it is the first item on the wire.
        items = [e["item"] for e in self.socket.events("conversation.item.create")]
        user = [i for i in items if i["role"] == "user"]
        self.assertEqual(len(user), 1)
        item = user[0]
        self.assertEqual(item["content"][0]["text"], "switch to workspace four")
        self.assertEqual(len(self.socket.events("response.create")), 1)

    async def test_local_confirm_releases_without_a_model_phrase(self):
        self.hold_a_reboot()
        output = await self.session._local_confirm()
        self.assertFalse(output.startswith("ERROR:"), output)
        self.assertIsNone(self.session.executor.pending)


class SessionUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_turn_detection_can_be_switched_off(self):
        session = realtime.RealtimeSession(Config(realtime_turn_detection="none"))
        self.assertIsNone(session._turn_detection())
        session = realtime.RealtimeSession(Config(realtime_turn_detection="server_vad"))
        self.assertEqual(session._turn_detection(), {"type": "server_vad"})

class DeadResponseTests(unittest.IsolatedAsyncioTestCase):
    """A turn that produces nothing must not look like not being heard.

    The session log has stretches of `heard ...` with no reply and no action —
    the user asking to switch workspace four times running and getting silence
    each time. Those were `response.done` events with status `failed`, which
    the engine dropped without a log line, a notification, or a word.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for name, value in (("LOG_FILE", root / "session.log"),
                            ("STATE_FILE", root / "state.json"),
                            ("STATE_DIR", root),
                            ("RUNTIME_DIR", root)):
            patcher = mock.patch.object(feedback, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.config = Config(dry_run=True, notify=False)
        self.session = realtime.RealtimeSession(self.config)
        self.socket = FakeSocket()
        self.session.ws = self.socket
        self.log = root / "session.log"

    def rate_limited(self, message="Rate limit reached. Please try again in 10ms."):
        return {"type": "response.done", "response": {
            "status": "failed",
            "status_details": {"type": "failed", "error": {
                "type": "tokens", "code": "rate_limit_exceeded", "message": message}}}}

    async def test_a_failed_response_is_logged(self):
        await self.session._on_response_done(self.rate_limited())
        self.assertIn("rate_limit_exceeded", self.log.read_text())

    async def test_a_rate_limited_turn_is_retried(self):
        await self.session._on_response_done(self.rate_limited())
        self.assertEqual(len(self.socket.events("response.create")), 1)

    async def test_retries_are_capped_then_the_user_is_told(self):
        for _ in range(realtime.RATE_LIMIT_RETRIES + 1):
            await self.session._on_response_done(self.rate_limited())
        self.assertEqual(len(self.socket.events("response.create")),
                         realtime.RATE_LIMIT_RETRIES)
        self.assertIn("rate limit", self.log.read_text().lower())

    async def test_a_new_user_turn_refills_the_retry_budget(self):
        for _ in range(realtime.RATE_LIMIT_RETRIES + 1):
            await self.session._on_response_done(self.rate_limited())
        await self.session._on_event({"type": "input_audio_buffer.speech_started"})
        self.assertEqual(self.session._rate_limit_retries, 0)

    async def test_a_non_rate_limit_failure_is_not_retried(self):
        await self.session._on_response_done({
            "type": "response.done",
            "response": {"status": "failed", "status_details": {
                "error": {"code": "server_error", "message": "boom"}}}})
        self.assertEqual(self.socket.events("response.create"), [])
        self.assertIn("server_error", self.log.read_text())

    async def test_a_completed_response_reports_nothing(self):
        await self.session._on_response_done(
            {"type": "response.done", "response": {"status": "completed", "output": []}})
        # Nothing logged at all: the file is only created on the first write.
        self.assertNotIn("error", self.log.read_text() if self.log.exists() else "")


class RetryAfterTests(unittest.TestCase):
    def test_milliseconds(self):
        self.assertAlmostEqual(realtime._retry_after("try again in 480ms"), 0.68, places=2)

    def test_seconds(self):
        self.assertAlmostEqual(realtime._retry_after("Please try again in 1.5s."), 1.7, places=2)

    def test_a_long_wait_is_clamped(self):
        self.assertEqual(realtime._retry_after("try again in 600s"), 8.0)

    def test_no_figure_falls_back(self):
        self.assertEqual(realtime._retry_after("rate limited"), realtime.RATE_LIMIT_PAUSE)


class StateRefreshTests(unittest.IsolatedAsyncioTestCase):
    """The desktop snapshot must not rewrite the cached instructions.

    Instructions are the cached prefix: persona, the capability manifest, the
    tool schemas. Rewriting them to carry a window list meant re-prefilling
    about 9k unchanged tokens on every single turn.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for name, value in (("LOG_FILE", root / "session.log"),
                            ("STATE_FILE", root / "state.json"),
                            ("STATE_DIR", root),
                            ("RUNTIME_DIR", root)):
            patcher = mock.patch.object(feedback, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.session = realtime.RealtimeSession(Config(dry_run=True, notify=False))
        self.socket = FakeSocket()
        self.session.ws = self.socket

    async def refresh(self):
        with mock.patch.object(realtime.capabilities, "live_state",
                               return_value="Workspaces in use: 1 (2 windows)"):
            self.session._state_refreshed = 0.0
            await self.session._refresh_state()

    async def test_the_snapshot_is_appended_not_written_into_instructions(self):
        await self.refresh()
        self.assertEqual(self.socket.events("session.update"), [])
        items = self.socket.events("conversation.item.create")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["item"]["role"], "system")

    async def test_the_snapshot_carries_the_live_state(self):
        await self.refresh()
        text = self.socket.events("conversation.item.create")[0]["item"]["content"][0]["text"]
        self.assertIn("Workspaces in use: 1 (2 windows)", text)

    async def test_a_typed_turn_refreshes_even_inside_the_interval(self):
        # `_instructions` stamps the clock at session start, so without the
        # force a turn in the first few seconds got no snapshot at all.
        self.session._state_refreshed = 1e18  # as if refreshed this instant
        with mock.patch.object(realtime.capabilities, "live_state", return_value="live"):
            await self.session._inject("what is open?")
        roles = [e["item"]["role"] for e in self.socket.events("conversation.item.create")]
        self.assertIn("system", roles)

    async def test_refreshes_are_rate_limited(self):
        await self.refresh()
        await self.session._refresh_state()  # straight away: too soon
        self.assertEqual(len(self.socket.events("conversation.item.create")), 1)

    async def test_the_previous_snapshot_is_deleted_not_stacked(self):
        await self.refresh()
        await self.refresh()
        creates = self.socket.events("conversation.item.create")
        deletes = self.socket.events("conversation.item.delete")
        self.assertEqual(len(creates), 2)
        self.assertEqual(len(deletes), 1)
        self.assertEqual(deletes[0]["item_id"], creates[0]["item"]["id"])

    async def test_a_typed_turn_gets_a_snapshot_too(self):
        with mock.patch.object(realtime.capabilities, "live_state",
                               return_value="Workspaces in use: 1 (2 windows)"):
            await self.session._inject("what is open?")
        items = self.socket.events("conversation.item.create")
        # snapshot first, then the user's words — order matters, the model reads
        # the desktop as context for the sentence rather than after it.
        self.assertEqual(items[0]["item"]["role"], "system")
        self.assertIn("Workspaces in use", items[0]["item"]["content"][0]["text"])
        self.assertEqual(items[1]["item"]["role"], "user")

    async def test_the_first_snapshot_deletes_nothing(self):
        await self.refresh()
        self.assertEqual(self.socket.events("conversation.item.delete"), [])

    async def test_the_manifest_is_not_rebuilt_per_turn(self):
        with mock.patch.object(realtime.capabilities, "manifest") as manifest:
            await self.refresh()
        manifest.assert_not_called()

class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    """A dropped websocket used to end the run, exit 0, and leave systemd
    thinking the service was healthy while the microphone key did nothing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for name, value in (("LOG_FILE", root / "session.log"),
                            ("STATE_FILE", root / "state.json"),
                            ("STATE_DIR", root), ("RUNTIME_DIR", root)):
            patcher = mock.patch.object(feedback, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.session = realtime.RealtimeSession(Config(dry_run=True, notify=False))
        self.session.ws = FakeSocket()

    async def test_a_send_failure_marks_the_connection_dropped(self):
        class Dead:
            async def send(self, raw):
                raise ConnectionError("keepalive ping timeout")
        self.session.ws = Dead()
        await self.session._send({"type": "ping"})
        self.assertTrue(self.session._dropped)
        self.assertEqual(self.session._exit_code, 1)
        self.assertTrue(self.session._stop.is_set())

    async def test_a_user_quit_is_not_a_drop(self):
        # _control hands work to the loop, so it needs one.
        self.session.loop = asyncio.get_running_loop()
        self.session._control("quit")
        self.assertFalse(self.session._dropped)
        self.assertTrue(self.session._user_quit)

    async def test_serve_reconnects_after_a_drop_then_stops_on_quit(self):
        attempts = []

        async def fake_session(url, headers):
            attempts.append(1)
            if len(attempts) < 3:
                self.session._dropped = True     # socket died
            else:
                self.session._user_quit = True   # user asked to stop
        with mock.patch.object(self.session, "_open_one", side_effect=fake_session), \
             mock.patch.object(realtime, "RECONNECT_BASE_DELAY", 0.01), \
             mock.patch.object(realtime, "RECONNECT_MAX_DELAY", 0.01):
            await self.session._serve("wss://x", {})
        self.assertEqual(len(attempts), 3)

    async def test_serve_gives_up_and_fails_after_the_cap(self):
        async def always_drops(url, headers):
            self.session._dropped = True
        with mock.patch.object(self.session, "_open_one", side_effect=always_drops), \
             mock.patch.object(realtime, "RECONNECT_ATTEMPTS", 2), \
             mock.patch.object(realtime, "RECONNECT_BASE_DELAY", 0.01), \
             mock.patch.object(realtime, "RECONNECT_MAX_DELAY", 0.01):
            await self.session._serve("wss://x", {})
        # Non-zero so Restart=on-failure gets its turn.
        self.assertEqual(self.session._exit_code, 1)
        self.assertIn("gave up", self.tmp.name and
                      (Path(self.tmp.name) / "session.log").read_text())

    async def test_listening_survives_a_reconnect(self):
        self.session.active = True
        self.session._active_event.set()
        calls = []

        async def drop_once(url, headers):
            calls.append(1)
            if len(calls) == 1:
                self.session._dropped = True
            else:
                self.session._user_quit = True
        with mock.patch.object(self.session, "_open_one", side_effect=drop_once), \
             mock.patch.object(realtime, "RECONNECT_BASE_DELAY", 0.01), \
             mock.patch.object(realtime, "RECONNECT_MAX_DELAY", 0.01):
            await self.session._serve("wss://x", {})
        # It went back to listening rather than coming up muted.
        self.assertTrue(self.session.active)




class AnnounceTests(unittest.IsolatedAsyncioTestCase):
    """Saying something without being asked.

    Everything else this daemon does is a reply. A build that ends on
    workspace two is the one thing it starts on its own, and the rules about
    when it may do that are the interesting part.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for name, value in (("LOG_FILE", root / "session.log"),
                            ("STATE_FILE", root / "state.json"),
                            ("STATE_DIR", root),
                            ("RUNTIME_DIR", root)):
            patcher = mock.patch.object(feedback, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.session = realtime.RealtimeSession(Config(dry_run=True, notify=False))
        self.socket = FakeSocket()
        self.session.ws = self.socket
        self.session._last_interruption = 0.0
        self.notified = []
        patcher = mock.patch.object(self.session.feedback, "notify",
                                    side_effect=lambda *a, **k: self.notified.append(a))
        patcher.start()
        self.addCleanup(patcher.stop)

    def job(self, **over):
        base = {"target": "Work:1.1", "label": "the test run", "seconds": 42.0,
                "vanished": False, "timed_out": False, "tail": "ALL TESTS PASSED"}
        return {**base, **over}

    async def test_a_muted_session_notifies_instead_of_speaking(self):
        """Talking into a room that is not listening is just noise."""
        self.session.active = False
        await self.session._announce(self.job())
        self.assertEqual(self.socket.sent, [])
        self.assertTrue(self.notified)

    async def test_a_listening_session_is_asked_to_speak(self):
        self.session.active = True
        await self.session._announce(self.job())
        self.assertTrue(self.socket.events("conversation.item.create"))
        self.assertTrue(self.socket.events("response.create"))

    async def test_the_output_goes_with_it_so_the_verdict_is_not_guessed(self):
        self.session.active = True
        await self.session._announce(self.job(tail="FAILED: 3 tests"))
        item = self.socket.events("conversation.item.create")[0]["item"]
        text = item["content"][0]["text"]
        self.assertIn("FAILED: 3 tests", text)
        self.assertIn("the test run", text)

    async def test_the_model_is_told_the_user_did_not_just_speak(self):
        """Without this it answers as though it had been asked something."""
        self.session.active = True
        await self.session._announce(self.job())
        text = self.socket.events("conversation.item.create")[0]["item"]["content"][0]["text"]
        self.assertIn("did not just speak", text)

    async def test_a_vanished_pane_is_described_as_closed_not_finished(self):
        self.session.active = True
        await self.session._announce(self.job(vanished=True))
        text = self.socket.events("conversation.item.create")[0]["item"]["content"][0]["text"]
        self.assertIn("closed", text)

    async def test_it_waits_rather_than_talking_over_a_reply_in_flight(self):
        self.session.active = True
        self.session._response_running = True

        async def release():
            await asyncio.sleep(0.05)
            self.session._response_running = False

        await asyncio.gather(self.session._announce(self.job()), release())
        self.assertTrue(self.socket.events("response.create"))

    async def test_announcements_do_not_pile_up_on_each_other(self):
        self.session.active = True
        self.session._last_interruption = time.time()
        with mock.patch.object(realtime, "WATCH_MIN_GAP_SECONDS", 0.05):
            await self.session._announce(self.job())
        self.assertTrue(self.socket.events("response.create"))

    async def test_a_stopping_session_says_nothing(self):
        self.session.active = True
        self.session._stop.set()
        self.session._response_running = True
        await self.session._announce(self.job())
        self.assertEqual(self.socket.events("response.create"), [])


class EchoGateTests(unittest.TestCase):
    """Her voice must not come back in as the user's.

    From a real session on speakers, with the mic and the line out on the same
    audio interface:

        reply  'OH-mah, OH-mah, OH-mah.'
        error  response cancelled: turn_detected
        heard  '\uc5b4\ub9c8'                <- her own name, back through the mic
        reply  'Yes, I'm here.'

    and, later, her own sentence returned as two user turns which she then
    apologised for not catching. A fragment that transcribes as an instruction
    is not merely noise: one arrived as 'Бела.' and pressed CTRL+R.
    """

    def setUp(self):
        self.speaker = realtime.Speaker(rate=24000)

    def test_a_fresh_speaker_is_not_playing(self):
        self.assertFalse(self.speaker.is_playing())

    def test_writing_audio_books_its_real_duration(self):
        """PCM16 mono: one second of 24 kHz is 48000 bytes."""
        with mock.patch.object(realtime.asyncio, "create_task"):
            asyncio.run(self.speaker.write(b"\0" * 48000))
        self.assertTrue(self.speaker.is_playing())
        self.assertAlmostEqual(self.speaker._plays_until - time.monotonic(), 1.0, delta=0.2)

    def test_chunks_queue_up_rather_than_overwriting_each_other(self):
        """The model sends a reply far faster than it is spoken, so the gate
        has to track the whole backlog, not the newest chunk."""
        with mock.patch.object(realtime.asyncio, "create_task"):
            for _ in range(3):
                asyncio.run(self.speaker.write(b"\0" * 24000))   # 0.5s each
        self.assertAlmostEqual(self.speaker._plays_until - time.monotonic(), 1.5, delta=0.3)

    def test_the_tail_keeps_the_gate_shut_a_little_longer(self):
        """A room rings after playback stops; the last syllable comes back late."""
        self.speaker._plays_until = time.monotonic() - 0.1
        self.assertFalse(self.speaker.is_playing())
        self.assertTrue(self.speaker.is_playing(realtime.ECHO_TAIL_SECONDS))

    def test_a_barge_in_reopens_the_microphone_at_once(self):
        """Dropping queued audio means nothing more is coming out, so the gate
        must not stay shut for audio that will never be played."""
        with mock.patch.object(realtime.asyncio, "create_task"):
            asyncio.run(self.speaker.write(b"\0" * 480000))       # 10 seconds
        self.assertTrue(self.speaker.is_playing())
        self.speaker._drop_queued()
        self.assertFalse(self.speaker.is_playing())

    def test_half_duplex_is_the_default(self):
        self.assertFalse(Config().barge_in)

    def test_barge_in_can_be_turned_back_on_for_headphones(self):
        self.assertTrue(Config(barge_in=True).barge_in)


class MicrophoneGateTests(unittest.IsolatedAsyncioTestCase):
    """Frames must not reach the server while her own voice is in the room.

    Everything this needs existed and nothing used it: ECHO_TAIL_SECONDS,
    Speaker.is_playing() and the _held_frames counter were all present, and
    the mic loop appended every frame unconditionally. So the EchoGateTests
    above passed -- they only ever ask Speaker what it thinks -- while on
    speakers she interrupted herself on every reply:

        reply  'Multiple Chrome app windows, plus Outlook, Teams...'
        heard  'Multiple crowd'          <- her own voice, back through the mic
        cancel reply cut short (turn_detected)

    eight times over, never finishing a sentence. These drive the loop itself,
    so deleting the gate fails them.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for name, value in (("LOG_FILE", root / "session.log"),
                            ("STATE_FILE", root / "state.json"),
                            ("STATE_DIR", root),
                            ("RUNTIME_DIR", root)):
            patcher = mock.patch.object(feedback, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def _session(self, barge_in):
        # silence_gate off on purpose. These frames are digital silence, and
        # this class is about the echo gate -- whether her own voice reaches the
        # server -- not about whether silence is worth paying to upload. With
        # both gates live these tests passed only because the silence gate's
        # hold window had not elapsed inside a fast test, which is agreement by
        # coincidence: the SilenceGateTests below own that behaviour.
        session = realtime.RealtimeSession(
            Config(dry_run=True, notify=False, barge_in=barge_in,
                   silence_gate=False))
        session.ws = FakeSocket()
        return session

    async def _run_mic_loop(self, session, frames):
        """Drive one capture with `frames`, then let the loop end on EOF."""
        reads = list(frames) + [b""]

        class FakeStdout:
            async def read(self, _n):
                return reads.pop(0) if reads else b""

        proc = mock.Mock()
        proc.stdout = FakeStdout()
        proc.returncode = None

        async def fake_exec(*_a, **_kw):
            return proc

        session._active_event.set()
        with mock.patch.object(realtime.asyncio, "create_subprocess_exec",
                               side_effect=fake_exec), \
             mock.patch.object(session, "_kill_mic",
                               new=mock.AsyncMock(side_effect=lambda: session._stop.set())):
            await asyncio.wait_for(session._mic_loop(), timeout=5)

    def _appended(self, session):
        return [e for e in session.ws.sent
                if e.get("type") == "input_audio_buffer.append"]

    async def test_her_own_voice_never_reaches_the_server(self):
        session = self._session(barge_in=False)
        session.speaker._plays_until = time.monotonic() + 5
        await self._run_mic_loop(session, [b"\0" * 100] * 3)
        self.assertEqual(self._appended(session), [],
                         "frames were sent while she was still speaking")
        self.assertEqual(session._held_frames, 3)

    async def test_a_quiet_room_is_sent_normally(self):
        session = self._session(barge_in=False)
        session.speaker._plays_until = 0.0           # nothing playing
        await self._run_mic_loop(session, [b"\0" * 100] * 3)
        self.assertEqual(len(self._appended(session)), 3)

    async def test_barge_in_sends_even_while_she_speaks(self):
        # With headphones her voice never reaches the mic, so holding frames
        # would only stop the user interrupting -- which is the setting's
        # entire purpose.
        session = self._session(barge_in=True)
        session.speaker._plays_until = time.monotonic() + 5
        await self._run_mic_loop(session, [b"\0" * 100] * 2)
        self.assertEqual(len(self._appended(session)), 2)

    async def test_the_gate_reopens_once_the_tail_has_passed(self):
        session = self._session(barge_in=False)
        session.speaker._plays_until = time.monotonic() - realtime.ECHO_TAIL_SECONDS - 0.1
        await self._run_mic_loop(session, [b"\0" * 100])
        self.assertEqual(len(self._appended(session)), 1)


if __name__ == "__main__":
    unittest.main()


def _loud(frames: int = 1) -> bytes:
    """A frame that reads as speech to `frame_level`.

    Amplitude matters and is not arbitrary: the level curve has a floor at 0.10
    of its raw value, so a quiet-but-nonzero buffer still reports 0.0 and would
    be gated as room tone. 8000 lands where the curve calls normal speech.
    """
    return array.array("h", [8000, -8000] * (frames * 50)).tobytes()


_SILENT = b"\0" * 100


class SilenceGateTests(unittest.IsolatedAsyncioTestCase):
    """Room tone is not worth paying to upload, but the pause after a sentence is.

    The mic loop used to append every frame while listening was on, so an open
    microphone in an empty room was billed at the same audio rate as speech.
    These drive the loop itself, so removing the gate fails them -- and so does
    a gate that is too eager, which is the more dangerous failure: cut the
    silence that follows a sentence and server-side turn detection never sees
    the pause, so the turn never ends and no reply ever comes.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for name, value in (("LOG_FILE", root / "session.log"),
                            ("STATE_FILE", root / "state.json"),
                            ("STATE_DIR", root),
                            ("RUNTIME_DIR", root)):
            patcher = mock.patch.object(feedback, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def _session(self, **kw):
        session = realtime.RealtimeSession(
            Config(dry_run=True, notify=False, barge_in=True, **kw))
        session.ws = FakeSocket()
        return session

    async def _run(self, session, frames):
        reads = list(frames) + [b""]

        class FakeStdout:
            async def read(self, _n):
                return reads.pop(0) if reads else b""

        proc = mock.Mock()
        proc.stdout = FakeStdout()
        proc.returncode = None

        async def fake_exec(*_a, **_kw):
            return proc

        session._active_event.set()
        with mock.patch.object(realtime.asyncio, "create_subprocess_exec",
                               side_effect=fake_exec), \
             mock.patch.object(session, "_kill_mic",
                               new=mock.AsyncMock(side_effect=lambda: session._stop.set())):
            await asyncio.wait_for(session._mic_loop(), timeout=5)

    def _appended(self, session):
        return [e for e in session.ws.sent
                if e.get("type") == "input_audio_buffer.append"]

    async def test_the_pause_after_speech_is_still_sent(self):
        """The turn-end pause is the one silence that must never be withheld."""
        session = self._session(silence_hold_seconds=60)
        await self._run(session, [_loud(), _SILENT, _SILENT, _SILENT])
        self.assertEqual(len(self._appended(session)), 4,
                         "the pause a turn ends on was withheld")

    async def test_sustained_silence_stops_being_sent(self):
        session = self._session(silence_hold_seconds=0)
        await self._run(session, [_SILENT] * 5)
        self.assertEqual(self._appended(session), [],
                         "an empty room was streamed to the API")
        self.assertEqual(session._gated_frames, 5)

    async def test_speech_resumes_with_its_first_syllable(self):
        """Pre-roll, or the gate eats the word that opened it."""
        session = self._session(silence_hold_seconds=0)
        quiet = [_SILENT] * realtime.PREROLL_FRAMES
        await self._run(session, [*quiet, _loud()])
        sent = self._appended(session)
        self.assertEqual(len(sent), realtime.PREROLL_FRAMES + 1,
                         "the buffered run-up was dropped instead of flushed")
        self.assertEqual(
            sent[-1]["audio"], base64.b64encode(_loud()).decode(),
            "pre-roll was flushed out of order — the speech frame must come last")

    async def test_the_gate_can_be_turned_off(self):
        session = self._session(silence_gate=False, silence_hold_seconds=0)
        await self._run(session, [_SILENT] * 3)
        self.assertEqual(len(self._appended(session)), 3)

    async def test_the_first_frame_of_a_capture_is_never_gated(self):
        """The toggle was pressed because someone is about to speak."""
        session = self._session(silence_hold_seconds=1.5)
        await self._run(session, [_SILENT])
        self.assertEqual(len(self._appended(session)), 1)


class IdleStopTests(unittest.IsolatedAsyncioTestCase):
    """An open microphone that has heard nothing for ten minutes stops itself."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for name, value in (("LOG_FILE", root / "session.log"),
                            ("STATE_FILE", root / "state.json"),
                            ("STATE_DIR", root),
                            ("RUNTIME_DIR", root)):
            patcher = mock.patch.object(feedback, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def _session(self, **kw):
        session = realtime.RealtimeSession(Config(dry_run=True, notify=False, **kw))
        session.ws = FakeSocket()
        return session

    async def test_silence_past_the_limit_switches_listening_off(self):
        session = self._session(idle_stop_seconds=60)
        session.active = True
        session._active_event.set()
        session._last_speech = time.monotonic() - 61
        self.assertTrue(await session._idle_stop(0.0))
        self.assertFalse(session.active, "listening stayed on past the idle limit")

    async def test_speech_resets_the_clock(self):
        session = self._session(idle_stop_seconds=60)
        session.active = True
        session._last_speech = time.monotonic() - 61
        self.assertFalse(await session._idle_stop(0.9),
                         "stopped while somebody was actually talking")
        self.assertTrue(session.active)

    async def test_zero_disables_it(self):
        session = self._session(idle_stop_seconds=0)
        session.active = True
        session._last_speech = time.monotonic() - 10_000
        self.assertFalse(await session._idle_stop(0.0))
        self.assertTrue(session.active)


class UsageLogTests(unittest.TestCase):
    """What a turn cost is in the log, or every cost claim is a guess."""

    def test_the_line_breaks_out_audio_and_cache(self):
        line = realtime.usage_line({
            "input_tokens": 11024, "output_tokens": 284,
            "input_token_details": {"cached_tokens": 9984, "text_tokens": 10412,
                                    "audio_tokens": 612},
            "output_token_details": {"text_tokens": 44, "audio_tokens": 240},
        })
        self.assertIn("audio 612", line)
        self.assertIn("cached 9984", line)
        self.assertIn("out 284", line)

    def test_a_response_with_no_detail_blocks_does_not_explode(self):
        self.assertIn("in 7", realtime.usage_line({"input_tokens": 7}))


class HistoryTrimTests(unittest.IsolatedAsyncioTestCase):
    """A long session must not pay for its whole history on every turn."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        for name, value in (("LOG_FILE", root / "session.log"),
                            ("STATE_FILE", root / "state.json"),
                            ("STATE_DIR", root),
                            ("RUNTIME_DIR", root)):
            patcher = mock.patch.object(feedback, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def _session(self, **kw):
        session = realtime.RealtimeSession(Config(dry_run=True, notify=False, **kw))
        session.ws = FakeSocket()
        return session

    def _deleted(self, session):
        return [e["item_id"] for e in session.ws.sent
                if e.get("type") == "conversation.item.delete"]

    async def test_the_oldest_items_go_first(self):
        session = self._session(history_items=3)
        session._history = [(f"item_{n}", "message") for n in range(6)]
        await session._trim_history()
        self.assertEqual(self._deleted(session), ["item_0", "item_1", "item_2"])
        self.assertEqual([i for i, _ in session._history],
                         ["item_3", "item_4", "item_5"])

    async def test_a_tool_result_is_never_orphaned_from_its_call(self):
        """Keeping an output whose call was deleted shows the model an answer
        to a question it cannot see it asked."""
        session = self._session(history_items=3)
        session._history = [
            ("item_0", "message"),
            ("item_1", "function_call"),
            ("item_2", "function_call_output"),
            ("item_3", "message"),
            ("item_4", "message"),
        ]
        await session._trim_history()
        self.assertNotIn("function_call_output", [t for _, t in session._history],
                         "an output outlived the call it answers")
        self.assertEqual([i for i, _ in session._history], ["item_3", "item_4"])

    async def test_a_short_conversation_is_left_alone(self):
        session = self._session(history_items=40)
        session._history = [(f"item_{n}", "message") for n in range(5)]
        await session._trim_history()
        self.assertEqual(self._deleted(session), [])

    async def test_zero_disables_trimming(self):
        session = self._session(history_items=0)
        session._history = [(f"item_{n}", "message") for n in range(100)]
        await session._trim_history()
        self.assertEqual(self._deleted(session), [])

    async def test_the_live_snapshot_is_left_to_its_own_lifecycle(self):
        session = self._session(history_items=1)
        session._state_item = "item_0"
        session._history = [("item_0", "message"), ("item_1", "message")]
        await session._trim_history()
        self.assertEqual(self._deleted(session), [],
                         "the snapshot would have been deleted twice")
