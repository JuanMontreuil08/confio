"""Verify call audio conversion, the dial request and the call state machine with fakes."""
import array
import importlib.util
import json
import math
from pathlib import Path
import sys
import types
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("phone_call", ROOT / "scripts" / "phone_call.py")
phone_call = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(phone_call)
VA_SPEC = importlib.util.spec_from_file_location("voice_agent", ROOT / "scripts" / "voice_agent.py")
voice_agent = importlib.util.module_from_spec(VA_SPEC)
VA_SPEC.loader.exec_module(voice_agent)


class FakeHTTP:
    def __init__(self):
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return types.SimpleNamespace(status_code=200, json=lambda: {"data": {"call_control_id": "cc-1"}})


class FakeCalls:
    """Records commands and pushes scripted Telnyx events when the call is dialed."""

    def __init__(self, server_ref, events):
        self.server_ref, self.script, self.hangups = server_ref, events, 0

    def dial(self, destination, public_url, token):
        for event in self.script:
            self.server_ref[0].events.put(event)
        return {"call_control_id": "cc-1"}

    def hangup(self, call_control_id):
        self.hangups += 1
        self.server_ref[0].events.put(("call.hangup", {"call_control_id": "cc-1", "hangup_cause": "normal_clearing"}))


class FakeLink:
    def __init__(self, frames=()):
        self.frames, self.modes, self.played = list(frames), [], []

    def call_mode(self, on):
        self.modes.append(on)

    def stream(self, pcm):
        self.played.append(pcm)

    def poll(self, timeout=0.01):
        return self.frames.pop(0) if self.frames else None


def event(kind, **payload):
    return kind, {"call_control_id": "cc-1", **payload}


class PhoneCallTests(unittest.TestCase):
    def server(self, events):
        ref = [None]
        calls = FakeCalls(ref, events)
        ref[0] = phone_call.PhoneServer(calls, "https://example.ngrok-free.dev",
                                        Ed25519PrivateKey.generate().public_key(), port=0)
        return ref[0], calls

    def test_mulaw_round_trip_keeps_a_voice_like_tone(self):
        tone = array.array("h", (int(8000 * math.sin(2 * math.pi * 300 * i / 16000)) for i in range(320)))
        phone = phone_call.to_phone(tone.tobytes())
        self.assertEqual(len(phone), 160)  # 20 ms at 8 kHz, one byte per sample
        back = array.array("h", phone_call.from_phone(phone))
        self.assertEqual(len(back), 320)
        error = max(abs(a - b) for a, b in zip(tone[40:280], back[40:280]))
        self.assertLess(error, 1200)

    def test_dial_request_uses_documented_stream_and_voicemail_options(self):
        http = FakeHTTP()
        calls = phone_call.TelnyxCalls("KEY_TEST", "conn-1", "+16065550100", session=http)
        calls.dial("+51999999999", "https://example.ngrok-free.dev/", "tok")
        url, kwargs = http.requests[0]
        body = kwargs["json"]
        self.assertEqual(url, "https://api.telnyx.com/v2/calls")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer KEY_TEST"})
        self.assertEqual(body["stream_url"], "wss://example.ngrok-free.dev/media/tok")
        self.assertEqual(body["webhook_url"], "https://example.ngrok-free.dev/webhooks/telnyx")
        self.assertEqual((body["stream_bidirectional_mode"], body["stream_bidirectional_codec"]), ("rtp", "PCMU"))
        self.assertEqual(body["answering_machine_detection"], "detect")
        self.assertIs(body["retry_on_timeout"], False)
        self.assertEqual((body["timeout_secs"], body["time_limit_secs"]), (30, 600))
        self.assertTrue(body["command_id"])

    def test_answered_call_bridges_audio_and_hangs_up_on_button(self):
        server, calls = self.server([event("call.initiated"), event("call.answered"),
                                     event("call.machine.detection.ended", result="human")])
        sent = []
        server.send_to_phone = sent.append
        server.speaker.put(b"\x01\x00" * 320)
        mic = b"\x10\x00" * 400  # call-mode voice frames are mono
        link = FakeLink([None, None, ("M", mic), ("H", b"")])
        # Speaker audio queued before dial is cleared; inject callee audio after events start.
        original = calls.dial

        def dial(*args):
            result = original(*args)
            server.speaker.put(b"\x02\x00" * 320)
            return result
        calls.dial = dial
        self.assertEqual(server.call(link, "+51999999999"), "ended")
        self.assertEqual(link.modes, [True, False])
        self.assertEqual(link.played, [b"\x02\x00" * 320])
        self.assertEqual(sent, [b"\x10\x00" * 320])  # 400 mono samples -> one 20 ms frame, rest buffered
        self.assertEqual(calls.hangups, 1)

    def test_no_answer_and_voicemail_outcomes(self):
        server, _ = self.server([event("call.initiated"), event("call.hangup", hangup_cause="no_answer")])
        self.assertEqual(server.call(FakeLink(), "+51999999999"), "no_answer")
        server, calls = self.server([event("call.answered"),
                                     event("call.machine.detection.ended", result="machine")])
        sent = []
        server.send_to_phone = sent.append
        link = FakeLink([("M", b"\x10\x00" * 400)])
        self.assertEqual(server.call(link, "+51999999999"), "machine")
        self.assertEqual((calls.hangups, sent), (1, []))  # voicemail never hears the microphone

    def test_button_before_answer_cancels(self):
        server, calls = self.server([event("call.initiated")])
        self.assertEqual(server.call(FakeLink([("H", b"")]), "+51999999999"), "cancelled")
        self.assertEqual(calls.hangups, 1)

    def test_wifi_disconnect_during_dial_hangs_up_the_known_call(self):
        server, calls = self.server([event("call.initiated")])
        link = FakeLink()
        def disconnected(on):
            raise ConnectionError("WiFi lost")
        link.call_mode = disconnected
        with self.assertRaises(ConnectionError):
            server.call(link, "+51999999999")
        self.assertEqual(calls.hangups, 1)

    def test_events_for_other_calls_are_ignored(self):
        server, _ = self.server([("call.hangup", {"call_control_id": "other"}), event("call.initiated"),
                                 event("call.hangup", hangup_cause="busy")])
        self.assertEqual(server.call(FakeLink(), "+51999999999"), "no_answer")


class CallToolTests(unittest.TestCase):
    def agent(self):
        agent = voice_agent.Agent.__new__(voice_agent.Agent)
        agent.contacts, agent.pending_call = {"Naty": "+51999999999"}, None
        return agent

    def test_saved_contact_is_matched_without_case_or_accents(self):
        agent = self.agent()
        self.assertEqual(agent.run_tool("call_contact", json.dumps({"name": "náty"})),
                         {"status": "calling", "contact": "Naty"})
        self.assertEqual(agent.pending_call, ("Naty", "+51999999999"))
        self.assertIn("error", agent.run_tool("call_contact", json.dumps({"name": "Naty"})))  # one per turn

    def test_unknown_names_and_numbers_are_never_dialed(self):
        agent = self.agent()
        for args in ({"name": "Pedro"}, {"name": "+51988888888"}, {"number": "+51988888888"}, {}):
            self.assertIn("error", agent.run_tool("call_contact", json.dumps(args)))
        self.assertIsNone(agent.pending_call)

    def test_call_tool_lists_saved_names_only(self):
        tool = voice_agent.call_tool(["Naty"])
        self.assertIn("Saved contacts: Naty.", tool["description"])
        self.assertNotIn("+51", json.dumps(tool))


if __name__ == "__main__":
    unittest.main()
