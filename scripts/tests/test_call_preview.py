"""Integration checks for serial -> call event -> local preview page."""

from contextlib import redirect_stdout
from functools import partial
import importlib.util
import io
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MODULE_PATH = Path(__file__).resolve().parents[1] / "call_preview.py"
SPEC = importlib.util.spec_from_file_location("call_preview", MODULE_PATH)
preview = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview)


class CallPreviewTests(unittest.TestCase):
    def test_browser_token_requires_local_host_and_same_origin_post(self):
        from unittest.mock import Mock
        service = Mock()
        service.session.return_value = {"login_token": "fake-short-lived-token"}
        server = preview.ThreadingHTTPServer(("127.0.0.1", 0),
            partial(preview.PreviewHandler, state=preview.PreviewState(), service=service))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            for headers in ({}, {"Origin": "https://untrusted.example"},
                            {"Host": "untrusted.example", "Origin": "http://untrusted.example"}):
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(url + "/api/session", data=b"{}", method="POST",
                        headers={"Content-Type": "application/json", **headers}))
                self.assertEqual(error.exception.code, 403)
            service.session.assert_not_called()
            with urlopen(Request(url + "/api/session", data=b"{}", method="POST",
                headers={"Content-Type": "application/json", "Origin": url})) as response:
                self.assertEqual(json.load(response)["login_token"], "fake-short-lived-token")
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            service.session.assert_called_once()
            with self.assertRaises(HTTPError):
                urlopen(url + "/api/session")
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_only_call_opens_browser(self):
        state = preview.PreviewState()
        with redirect_stdout(io.StringIO()), patch.object(preview.webbrowser, "open") as browser:
            for line in ("Ver ejecutado", "", "boot log", "Llamar conectado extra"):
                self.assertFalse(state.receive(line, "http://localhost:8765/"))
            browser.assert_not_called()
            self.assertTrue(state.receive("Llamar conectado", "http://localhost:8765/"))
            browser.assert_called_once_with("http://localhost:8765/", new=0, autoraise=True)
        self.assertEqual(state.snapshot()["call_event"], 1)

    def test_usb_serial_message_reaches_http_status(self):
        state = preview.PreviewState()
        master, slave = os.openpty()
        shutdown = threading.Event()
        handler = partial(preview.PreviewHandler, state=state)
        server = preview.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        url = f"http://127.0.0.1:{server.server_port}"
        http_thread = threading.Thread(target=server.serve_forever, daemon=True)
        serial_thread = threading.Thread(target=preview.watch_serial, args=(state, url, os.ttyname(slave), False, shutdown), daemon=True)
        http_thread.start()
        try:
            with redirect_stdout(io.StringIO()):
                serial_thread.start()
                self.wait_for(lambda: state.snapshot()["connected"])
                os.write(master, b"Ver ejecutado\r\nLlamar conectado\r\n")
                self.wait_for(lambda: state.snapshot()["call_event"] == 1)
                with urlopen(url + "/api/status") as response:
                    self.assertEqual(json.load(response), {"connected": True, "call_event": 1})
                with urlopen(url + "/") as response:
                    self.assertIn("Talk with your agent", response.read().decode())
                with urlopen(url + "/assets/profile.png") as response:
                    self.assertEqual(response.read()[:8], b"\x89PNG\r\n\x1a\n")
                for path in ("/.env", "/../../.env", "/assets/"):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(url + path)
                    self.assertEqual(error.exception.code, 404)
                shutdown.set()
                serial_thread.join(timeout=3)
                self.assertFalse(serial_thread.is_alive())
        finally:
            shutdown.set()
            os.close(master)
            os.close(slave)
            server.shutdown()
            server.server_close()
            http_thread.join(timeout=3)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail("Timed out waiting for the serial bridge")


if __name__ == "__main__":
    unittest.main()
