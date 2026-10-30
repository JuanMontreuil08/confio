#!/usr/bin/env python3
"""Receive signed Telnyx events for the local call test. Never reads .env."""

import argparse
import base64
from collections import deque
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY_BYTES = 65536
MAX_EVENT_IDS = 1024


class EventReceiver:
    def __init__(self, public_key, clock=time.time):
        self.public_key = public_key
        self.clock = clock
        self.lock = threading.Lock()
        self.seen = set()
        self.order = deque()

    def receive(self, body, signature, timestamp):
        try:
            if abs(self.clock() - int(timestamp)) > 300:
                raise ValueError("Expired signature")
            self.public_key.verify(
                base64.b64decode(signature, validate=True),
                timestamp.encode("ascii") + b"|" + body,
            )
        except (InvalidSignature, ValueError, TypeError, UnicodeError):
            return 403, {"error": "Invalid webhook signature"}

        try:
            envelope = json.loads(body)
            event = envelope["data"]
            event_id, event_type = event["id"], event["event_type"]
            if not all(isinstance(value, str) and 0 < len(value) <= 256
                       for value in (event_id, event_type)):
                raise ValueError("Invalid event metadata")
            payload = event.get("payload", {})
            if not isinstance(payload, dict):
                raise ValueError("Invalid event payload")
        except (ValueError, TypeError, KeyError):
            return 400, {"error": "Invalid webhook event"}

        with self.lock:
            if event_id in self.seen:
                return 200, {"received": True, "duplicate": True}
            if len(self.order) >= MAX_EVENT_IDS:
                self.seen.remove(self.order.popleft())
            self.seen.add(event_id)
            self.order.append(event_id)

        # Keep debugging metadata, never full payloads, phone numbers or secrets.
        record = {"event": event_type, "event_id": event_id}
        for field in ("call_control_id", "hangup_cause", "hangup_source"):
            value = payload.get(field)
            if isinstance(value, str):
                record[field] = value[:512]
        print(json.dumps(record, ensure_ascii=True), flush=True)
        return 200, {"received": True}


class WebhookHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, receiver, **kwargs):
        self.receiver = receiver
        super().__init__(*args, **kwargs)

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def reply(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            self.reply(200, {"ok": True})
        else:
            self.reply(404, {"error": "Not found"})

    def do_POST(self):
        if self.path != "/webhooks/telnyx":
            self.reply(404, {"error": "Not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.reply(400, {"error": "Invalid content length"})
            return
        if not 0 < size <= MAX_BODY_BYTES:
            self.reply(413, {"error": "Invalid event size"})
            return
        try:
            body = self.rfile.read(size)
        except OSError:
            self.reply(408, {"error": "Request timed out"})
            return
        if len(body) != size:
            self.reply(400, {"error": "Incomplete event"})
            return
        status, response = self.receiver.receive(
            body,
            self.headers.get("telnyx-signature-ed25519", ""),
            self.headers.get("telnyx-timestamp", ""),
        )
        self.reply(status, response)

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--public-key-file", type=Path, default=ROOT / ".telnyx-public-key")
    args = parser.parse_args()
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(args.public_key_file.read_text().strip(), validate=True)
        )
    except (OSError, ValueError) as error:
        parser.exit(1, f"No se pudo cargar la clave pública de Telnyx: {error}\n")
    receiver = EventReceiver(public_key)
    try:
        server = ThreadingHTTPServer(
            ("127.0.0.1", args.port), partial(WebhookHandler, receiver=receiver)
        )
    except OSError as error:
        parser.exit(1, f"No se pudo iniciar el webhook: {error}\n")
    print(f"Webhook local: http://127.0.0.1:{server.server_port}/webhooks/telnyx", flush=True)
    print("Solo recibe eventos firmados. No lee .env ni inicia llamadas. Ctrl+C para detener.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nWebhook detenido.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
