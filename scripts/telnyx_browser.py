"""Server-only WebRTC provisioning and token minting. Never logs credentials."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi
from dotenv import load_dotenv

from telnyx_call import phone_number

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / ".telnyx-webrtc.json"
PROFILE_ID = "3081866635162682559"


class ProviderError(Exception):
    pass


class TelnyxAPI:
    def __init__(self, key):
        self.key = key

    def request(self, method, path, payload=None, text=False):
        request = Request("https://api.telnyx.com/v2" + path,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Authorization": "Bearer " + self.key,
                                   "Content-Type": "application/json"}, method=method)
        try:
            with urlopen(request, timeout=20, context=ssl.create_default_context(
                    cafile=certifi.where())) as response:
                body = response.read().decode()
            return body.strip() if text else json.loads(body)
        except HTTPError as error:
            raise ProviderError(f"Telnyx HTTP {error.code}. Check the Telnyx portal.") from None
        except (URLError, OSError, ValueError):
            raise ProviderError("Telnyx response unavailable. Check the portal before retrying setup.") from None


class BrowserService:
    def __init__(self, api, source, destination, state_file=STATE_FILE):
        self.api, self.source, self.destination = api, source, destination
        self.state_file = state_file
        self.lock = threading.Lock()
        self.token = None
        self.token_until = 0

    @classmethod
    def from_local_env(cls, destination):
        # Only called explicitly by the live server, with the user's permission.
        load_dotenv(ROOT / ".env", override=False)
        key = os.environ.get("TELNYX_API_KEY", "").strip()
        source = os.environ.get("TELNYX_FROM_NUMBER", "").strip()
        if not key or not source:
            raise ProviderError("Configure TELNYX_API_KEY and TELNYX_FROM_NUMBER in .env.")
        return cls(TelnyxAPI(key), phone_number(source), phone_number(destination))

    def save(self, state):
        # IDs and expiration only, never passwords, API keys or JWTs.
        temp = self.state_file.with_suffix(".tmp")
        with open(temp, "w", opener=lambda path, flags: os.open(path, flags, 0o600)) as output:
            json.dump(state, output)
        temp.replace(self.state_file)

    def setup(self):
        state = json.loads(self.state_file.read_text()) if self.state_file.exists() else {}
        if not state.get("connection_id"):
            # Recover a previous setup if the process stopped after creation.
            connections = self.api.request("GET", "/credential_connections?page[size]=250")["data"]
            existing = next((item for item in connections
                             if item.get("connection_name") == "Confio Browser Test"), None)
            if existing:
                state["connection_id"] = existing["id"]
            else:
                connection = self.api.request("POST", "/credential_connections", {
                    "connection_name": "Confio Browser Test", "active": True,
                    "user_name": "confio" + secrets.token_hex(10),
                    "password": secrets.token_urlsafe(32),
                    "outbound": {"outbound_voice_profile_id": PROFILE_ID,
                                 "call_parking_enabled": False, "channel_limit": 1,
                                 "ani_override": self.source, "ani_override_type": "always"},
                })["data"]
                state["connection_id"] = connection["id"]
            self.save(state)
        # Reuse today's credential; renew only when close to expiration.
        if not state.get("credential_id") or state.get("expires_epoch", 0) < time.time() + 3600:
            expiry = datetime.now(timezone.utc) + timedelta(hours=24)
            credential = self.api.request("POST", "/telephony_credentials", {
                "connection_id": state["connection_id"], "name": "Confio laptop test",
                "tag": "confio-local-test", "expires_at": expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            })["data"]
            state.update(credential_id=credential["id"], expires_epoch=expiry.timestamp())
            self.save(state)
            self.ready_at = time.monotonic() + 5
        else:
            self.ready_at = time.monotonic()
        self.credential_id = state["credential_id"]
        print(f"WebRTC configurado. Conexión: {state['connection_id']}. Sin iniciar llamadas.", flush=True)

    def session(self):
        with self.lock:
            delay = self.ready_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            if not self.token or time.monotonic() >= self.token_until:
                self.token = self.api.request(
                    "POST", f"/telephony_credentials/{self.credential_id}/token", text=True)
                self.token_until = time.monotonic() + 300
            return {"login_token": self.token, "from": self.source, "to": self.destination}
