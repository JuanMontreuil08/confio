"""No .env, hardware or network: exercise the photo/SMS sequence with fake APIs."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from PIL import Image
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from photo_tools import PhotoTools
import voice_agent
from test_voice_agent import FakeItem, fake_client, text_response, tool_response, FakeSession, FakeResponse


def image_bytes():
    out = io.BytesIO()
    Image.new("RGB", (16, 12), "blue").save(out, "JPEG")
    return out.getvalue()


class HTTP:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def call(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def get(self, url, **kwargs):
        return self.call("get", url, **kwargs)

    def post(self, url, **kwargs):
        return self.call("post", url, **kwargs)


def sms(status="queued"):
    return FakeResponse(body={"data": {"id": "sms-test", "to": [{"status": status}], "parts": 3}})


class PhotoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = {
            "CAMERA_URL": "http://192.168.1.50", "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SECRET_KEY": "sb_secret_fake", "SUPABASE_STORAGE_BUCKET": "photos",
            "TELNYX_API_KEY": "fake-telnyx", "TELNYX_MESSAGING_PROFILE_ID": "test-profile",
            "TELNYX_SMS_SENDER": "Confio",
        }
        self.edits = []

        def edit(**kwargs):
            self.edits.append(kwargs)
            return types.SimpleNamespace(data=[types.SimpleNamespace(b64_json=base64.b64encode(image_bytes()).decode())])
        self.client = types.SimpleNamespace(images=types.SimpleNamespace(edit=edit))

    def service(self, responses):
        # The first two camera buffers are discarded by take_photo.
        if responses and getattr(responses[0], "content", b""):
            responses = [FakeResponse(content=b"old frame"), FakeResponse(content=b"old frame")] + responses
        self.http = HTTP(responses)
        return PhotoTools(self.config, "fake-meta", Path(self.temp.name), {"Naty": "+51999999999"},
                          self.http, self.client)

    def capture(self, service):
        result = service.run("take_photo", "{}")
        self.assertEqual(result["status"], "restored")
        return result["photo_id"]

    def test_full_pipeline_preserves_original_sends_only_link_and_checks_delivery(self):
        service = self.service([FakeResponse(content=image_bytes()), FakeResponse(), FakeResponse(),
                               FakeResponse(body={"signedURL": "/object/sign/photos/test/restored.jpg?token=fake"}),
                               sms(), sms("delivered")])
        photo_id = self.capture(service)
        args = json.dumps({"photo_id": photo_id})
        self.assertEqual(service.run("save_photo", args)["status"], "saved")
        self.assertEqual(service.run("save_photo", args)["status"], "saved")  # no second upload
        send_args = json.dumps({"photo_id": photo_id, "contact": "náty"})
        first = service.run("send_photo_sms", send_args)
        self.assertEqual(first["status"], "queued")
        self.assertFalse(first["delivered"])
        self.assertNotIn("token=", json.dumps(first))
        final = service.run("send_photo_sms", send_args)
        self.assertTrue(final["delivered"])
        posts = [c for c in self.http.calls if c[0] == "post" and c[1] == "https://api.telnyx.com/v2/messages"]
        self.assertEqual(len(posts), 1)
        payload = posts[0][2]["json"]
        self.assertEqual(payload["to"], "+51999999999")
        self.assertEqual(payload["type"], "SMS")
        self.assertNotIn("media_urls", payload)
        self.assertIn("https://example.supabase.co/storage/v1/object/sign/", payload["text"])
        self.assertEqual(self.edits[0]["model"], "muse-image-1.0")
        self.assertTrue((Path(self.temp.name) / photo_id / "original.jpg").exists())
        self.assertTrue((Path(self.temp.name) / photo_id / "restored.jpg").exists())
        self.assertEqual(self.http.calls[3][2]["headers"]["apikey"], "sb_secret_fake")
        self.assertNotIn("Authorization", self.http.calls[3][2]["headers"])
        self.assertEqual((Path(self.temp.name) / photo_id / "original.jpg").read_bytes(), image_bytes())

    def test_capture_and_restore_failures_do_not_make_a_sendable_photo(self):
        service = self.service([FakeResponse(content=b"not an image")])
        self.assertIn("error", service.run("take_photo", "{}"))
        self.assertEqual(service.photos, {})
        self.assertEqual(self.edits, [])
        service = self.service([FakeResponse(content=image_bytes())])
        self.client.images.edit = lambda **kwargs: types.SimpleNamespace(data=[])
        self.assertIn("error", service.run("take_photo", "{}"))
        self.assertEqual(service.photos, {})

    def test_saved_contact_and_saved_photo_are_required(self):
        service = self.service([FakeResponse(content=image_bytes())])
        photo_id = self.capture(service)
        for name in ("Naty", "+51988888888", "Nobody"):
            result = service.run("send_photo_sms", json.dumps({"photo_id": photo_id, "contact": name}))
            self.assertIn("error", result)
        self.assertEqual(len(self.http.calls), 3)

    def test_sms_timeout_never_resends_or_claims_delivery(self):
        service = self.service([FakeResponse(content=image_bytes()), FakeResponse(), FakeResponse(),
                               FakeResponse(body={"signedURL": "/object/sign/photos/x.jpg?token=fake"}),
                               requests.Timeout("private-url-or-number")])
        photo_id = self.capture(service)
        service.run("save_photo", json.dumps({"photo_id": photo_id}))
        args = json.dumps({"photo_id": photo_id, "contact": "Naty"})
        result = service.run("send_photo_sms", args)
        self.assertIn("error", result)
        self.assertNotIn("private-url-or-number", json.dumps(result))
        self.assertEqual(service.run("send_photo_sms", args)["status"], "unknown")
        self.assertEqual(len(self.http.calls), 7)

    def test_partial_upload_is_reused_on_retry(self):
        service = self.service([FakeResponse(content=image_bytes()), FakeResponse(), FakeResponse(status=503), FakeResponse()])
        photo_id = self.capture(service)
        args = json.dumps({"photo_id": photo_id})
        self.assertIn("error", service.run("save_photo", args))
        self.assertFalse(service.photos[photo_id]["saved"])
        self.assertEqual(service.run("save_photo", args)["status"], "saved")
        self.assertEqual(sum("original.jpg" in c[1] for c in self.http.calls), 1)

    def test_malformed_arguments_are_not_executed(self):
        service = self.service([])
        for args in ("oops", "[]", '{"url":"http://example.org"}'):
            self.assertIn("error", service.run("take_photo", args))
        self.assertEqual(self.http.calls, [])

    def test_voice_preparation_then_capture_save_send_and_delivery_followup(self):
        service = self.service([FakeResponse(content=image_bytes()), FakeResponse(), FakeResponse(),
                               FakeResponse(body={"signedURL": "/object/sign/photos/one.jpg?token=fake"}),
                               sms(), sms("delivered")])
        agent = voice_agent.Agent("fake-meta", "fake-cartesia", "voice", "system", Path(self.temp.name),
                                 contacts={"Naty": "+51999999999"}, photos=service, enable_calls=False)
        responses = [text_response("Apunta la cámara y avísame cuando estés listo."),
                     tool_response("take_photo"),
                     tool_response("save_photo", '{"photo_id":"photo-one"}'),
                     tool_response("send_photo_sms", '{"photo_id":"photo-one","contact":"Naty"}'),
                     text_response("La foto está guardada y el SMS está en cola."),
                     text_response("Sí, el SMS fue entregado a Naty.")]
        agent.client, chat_requests = fake_client(*responses)
        audio = b"\x01\x00" * 1600
        agent.session = FakeSession(
            FakeResponse(body={"transcript": "Quiero tomar una foto y mandarla a Naty"}), FakeResponse(content=audio),
            FakeResponse(body={"transcript": "Ya estoy listo, tómala"}), FakeResponse(content=audio),
            FakeResponse(body={"transcript": "¿Ya llegó?"}), FakeResponse(content=audio))
        pcm = b"\x00\x00" * 16000
        self.assertEqual(agent.handle(pcm), audio)
        self.assertEqual(self.http.calls, [])  # no photo while preparing
        with patch("photo_tools.uuid4", return_value=types.SimpleNamespace(hex="photo-one")):
            self.assertEqual(agent.handle(pcm), audio)
        self.assertEqual(len(self.edits), 1)
        self.assertEqual(agent.handle(pcm), audio)
        latest = chat_requests[-1]["input"]
        updates = [m["content"] for m in latest if m.get("role") == "developer"]
        self.assertTrue(any('"status": "delivered"' in m for m in updates))
        self.assertEqual(sum(c[0] == "post" and c[1] == "https://api.telnyx.com/v2/messages"
                             for c in self.http.calls), 1)
        self.assertFalse(any(t.get("name") == "call_contact" for t in agent.tools))


class ConversationTests(unittest.TestCase):
    def agent(self):
        agent = voice_agent.Agent("fake-meta", "fake-cartesia", "voice", "system", Path("/tmp"))
        return agent

    def test_completed_response_and_reasoning_survive_into_next_user_turn(self):
        reasoning = FakeItem(type="reasoning", encrypted_content="opaque", summary=[])
        initial = text_response("Avísame cuando estés listo.")
        initial.output.insert(0, reasoning)
        client, requests_ = fake_client(initial, text_response("Listo."))
        history = []
        voice_agent.answer(client, "system", "Quiero una foto para Naty", history=history)
        voice_agent.answer(client, "system", "Ya, ahora", history=history)
        second = requests_[1]["input"]
        self.assertIn({"type": "reasoning", "encrypted_content": "opaque", "summary": []}, second)
        self.assertEqual(second[-1]["content"], "Ya, ahora")
        self.assertTrue(any(x.get("role") == "assistant" for x in second))
        self.assertEqual(sum(x.get("role") == "system" for x in history), 1)

    def test_past_tool_calls_are_never_executed_again(self):
        client, _ = fake_client(tool_response("save_photo", '{"photo_id":"one"}'),
                                text_response("Guardada."), text_response("Sí, ya la guardé."))
        executed = []
        def run(name, args):
            executed.append(name)
            return {"status": "saved"}
        history = []
        voice_agent.answer(client, "system", "Guarda", run=run, history=history)
        voice_agent.answer(client, "system", "¿La guardaste?", run=run, history=history)
        self.assertEqual(executed, ["save_photo"])

    def test_idle_boundary_is_measured_at_recording_start(self):
        agent = self.agent()
        agent.history = [{"role": "user", "content": "pending photo"}]
        agent.idle_since = 100
        with patch.object(voice_agent.time, "monotonic", return_value=900):
            agent.begin_turn(started_at=399)  # recording began before the deadline
        self.assertTrue(agent.history)
        self.assertIsNone(agent.idle_since)
        agent.idle_since = 100
        agent.begin_turn(started_at=400)
        self.assertEqual(agent.history, [])

    def test_call_outcome_records_actual_status(self):
        agent = self.agent()
        agent.remember_call_outcome("Naty", "no_answer")
        self.assertIn('"status": "no_answer"', agent.history[-1]["content"])

    def test_take_photo_is_not_repeated_within_a_turn(self):
        agent = self.agent()
        agent.photos = types.SimpleNamespace(run=lambda *args: {"photo_id": "one", "status": "restored"})
        first = agent.run_tool("take_photo", "{}")
        agent.photos.run = lambda *args: self.fail("Duplicate capture")
        self.assertEqual(agent.run_tool("take_photo", "{}"), first)


if __name__ == "__main__":
    unittest.main()
