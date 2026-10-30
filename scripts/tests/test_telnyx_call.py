"""Check the dial boundary with fake credentials and no network or .env reads."""

import argparse
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import URLError

SPEC = importlib.util.spec_from_file_location(
    "telnyx_call", Path(__file__).resolve().parents[1] / "telnyx_call.py"
)
call = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(call)


class CallTests(unittest.TestCase):
    def test_international_number_validation(self):
        self.assertEqual(call.phone_number("51999999999"), "+51999999999")
        self.assertEqual(call.phone_number("+51999999999"), "+51999999999")
        for value in ("", "051999999999", "abc", "+51;echo", "1" * 16):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                call.phone_number(value)

    def test_single_bounded_request(self):
        response = MagicMock()
        response.__enter__.return_value = io.StringIO('{"data":{"call_control_id":"test"}}')
        with patch.object(call, "urlopen", return_value=response) as send:
            self.assertEqual(call.dial("fake-key", "123", "+16064318045", "+51999999999"),
                             {"call_control_id": "test"})
        send.assert_called_once()
        request = send.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.full_url, "https://api.telnyx.com/v2/calls")
        self.assertEqual(payload["to"], "+51999999999")
        self.assertEqual(payload["from"], "+16064318045")
        self.assertEqual(payload["time_limit_secs"], 30)
        self.assertFalse(payload["retry_on_timeout"])
        self.assertNotIn("record", payload)

    def test_uncertain_network_result_is_not_retried_or_leaked(self):
        env = {"TELNYX_API_KEY": "fake-secret", "TELNYX_CONNECTION_ID": "123",
               "TELNYX_FROM_NUMBER": "+16064318045"}
        output = io.StringIO()
        with patch.object(call, "load_dotenv"), patch.dict(call.os.environ, env, clear=True), \
                patch.object(call, "urlopen", side_effect=URLError("fake-secret")) as send, \
                redirect_stdout(output), redirect_stderr(output):
            self.assertEqual(call.main(["51999999999"]), 1)
        send.assert_called_once()
        self.assertNotIn("fake-secret", output.getvalue())
        self.assertIn("podría haberse iniciado", output.getvalue())


if __name__ == "__main__":
    unittest.main()
