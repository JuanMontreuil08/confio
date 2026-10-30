"""Provisioning checks: fake API only; never loads local credentials."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telnyx_browser import BrowserService


class BrowserSetupTests(unittest.TestCase):
    def test_create_and_reuse_without_storing_secrets(self):
        api = Mock()
        api.request.side_effect = [
            {"data": []}, {"data": {"id": "connection-1", "password": "secret"}},
            {"data": {"id": "credential-1", "sip_password": "secret"}},
        ]
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            path = Path(directory) / "state.json"
            service = BrowserService(api, "+16064318045", "+51999999999", path)
            service.setup()
            state = path.read_text()
            self.assertNotIn("secret", state)
            self.assertEqual(json.loads(state)["credential_id"], "credential-1")
            connection_payload = api.request.call_args_list[1].args[2]
            self.assertFalse(connection_payload["outbound"]["call_parking_enabled"])
            self.assertEqual(connection_payload["outbound"]["channel_limit"], 1)
            api.reset_mock()
            service.setup()
            api.request.assert_not_called()
            api.request.return_value = "fake-jwt"
            api.request.side_effect = None
            self.assertEqual(service.session()["login_token"], "fake-jwt")
            service.session()
            api.request.assert_called_once_with("POST", "/telephony_credentials/credential-1/token", text=True)


if __name__ == "__main__":
    unittest.main()
