"""Exercise the Telnyx signature boundary without contacting Telnyx."""

import base64
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SPEC = importlib.util.spec_from_file_location(
    "telnyx_webhook", Path(__file__).resolve().parents[1] / "telnyx_webhook.py"
)
webhook = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(webhook)


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.private = Ed25519PrivateKey.generate()
        self.receiver = webhook.EventReceiver(self.private.public_key(), clock=lambda: 1000)
        self.body = json.dumps({"data": {
            "id": "event-1", "event_type": "call.answered",
            "payload": {"call_control_id": "call-1", "to": "+51999999999"},
        }}).encode()

    def signature(self, body=None, timestamp="1000", private=None):
        return base64.b64encode((private or self.private).sign(
            timestamp.encode() + b"|" + (self.body if body is None else body)
        )).decode()

    def test_signed_event_is_logged_once_without_phone_numbers(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.receiver.receive(self.body, self.signature(), "1000")[0], 200)
            self.assertTrue(self.receiver.receive(self.body, self.signature(), "1000")[1]["duplicate"])
        self.assertIn('"event": "call.answered"', output.getvalue())
        self.assertEqual(output.getvalue().count("\n"), 1)
        self.assertNotIn("+51999999999", output.getvalue())

    def test_tampered_expired_and_foreign_signatures_are_rejected(self):
        cases = [
            (self.body + b" ", self.signature(), "1000"),
            (self.body, self.signature(timestamp="600"), "600"),
            (self.body, self.signature(private=Ed25519PrivateKey.generate()), "1000"),
            (self.body, "invalid", "1000"),
            (self.body, "", ""),
        ]
        for body, signature, timestamp in cases:
            with self.subTest(timestamp=timestamp, signature=signature[:8]):
                self.assertEqual(self.receiver.receive(body, signature, timestamp)[0], 403)
        self.assertEqual(len(self.receiver.seen), 0)

    def test_invalid_signed_json_does_not_get_accepted(self):
        for body in (b"not-json", b"[]", b'{"data": {}}', b'{"data": {"id": 1}}'):
            self.assertEqual(self.receiver.receive(body, self.signature(body=body), "1000")[0], 400)


if __name__ == "__main__":
    unittest.main()
