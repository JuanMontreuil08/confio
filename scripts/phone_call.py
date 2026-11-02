"""Phone calls for the voice agent: Telnyx Call Control plus bidirectional media streaming.

The agent dials a saved contact through the Voice API. Telnyx sends signed call
events to POST /webhooks/telnyx and opens a WebSocket to /media/<per-call token>
carrying the callee's audio (G.711 mu-law, 8 kHz). The ESP32 microphone is sent
back on the same socket once a person (not voicemail) answers. Both paths are
reached through one public HTTPS URL, such as an ngrok tunnel to this server.

Docs: https://developers.telnyx.com/api-reference/call-commands/dial
      https://developers.telnyx.com/docs/voice/programmable-voice/media-streaming
      https://developers.telnyx.com/docs/voice/programmable-voice/answering-machine-detection

No audio is recorded and no phone numbers are logged.
"""
import array
import asyncio
import base64
import hmac
import json
import logging
import queue
import secrets
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

import requests
from aiohttp import web

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "esp32" / "voice"))
from voice_link import READY_MESSAGE  # noqa: E402
from telnyx_webhook import EventReceiver  # noqa: E402

log = logging.getLogger("voice_agent")

TELNYX_API = "https://api.telnyx.com/v2"
RING_SECONDS = 30  # Telnyx hangs up unanswered calls after this (hangup_cause no_answer)
CALL_LIMIT_SECONDS = 600  # Telnyx ends answered calls after 10 minutes
EVENT_TIMEOUT = 15  # local guard: no call event at all means webhooks are unreachable
RING_GUARD = RING_SECONDS + 20  # local guard in case Telnyx's own ring timeout is never reported
AMD_FALLBACK_SECONDS = 6  # treat as a person if the voicemail check never reports
HANGUP_WAIT = 10
PHONE_FRAME_BYTES = 640  # 20 ms of 16 kHz 16-bit mono, sent as 160 mu-law bytes

# ---- G.711 mu-law (Python 3.13 removed audioop) ----
_BIAS, _CLIP = 0x84, 32635


def _ulaw_encode(sample):
    sign = 0x80 if sample < 0 else 0
    sample = min(abs(sample), _CLIP) + _BIAS
    exponent = max((sample >> 7).bit_length() - 1, 0)
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | exponent << 4 | mantissa) & 0xFF


def _ulaw_decode(value):
    value = ~value & 0xFF
    sample = (((value & 0x0F) << 3) + _BIAS << ((value >> 4) & 7)) - _BIAS
    return -sample if value & 0x80 else sample


ULAW_DECODE = [_ulaw_decode(i) for i in range(256)]


def to_phone(pcm16k):
    """16 kHz 16-bit PCM -> 8 kHz mu-law (averages sample pairs as a simple low-pass)."""
    samples = array.array("h", pcm16k[:len(pcm16k) - len(pcm16k) % 4])
    return bytes(_ulaw_encode((samples[i] + samples[i + 1]) // 2) for i in range(0, len(samples), 2))


def from_phone(ulaw):
    """8 kHz mu-law -> 16 kHz 16-bit PCM (linear interpolation)."""
    decoded = [ULAW_DECODE[b] for b in ulaw]
    out = array.array("h")
    for i, sample in enumerate(decoded):
        following = decoded[i + 1] if i + 1 < len(decoded) else sample
        out.extend((sample, (sample + following) // 2))
    return out.tobytes()


class TelnyxCalls:
    """Exactly one HTTP request per command: never automatically retry a billable call."""

    def __init__(self, api_key, connection_id, source, session=None):
        self.api_key, self.connection_id, self.source = api_key, connection_id, source
        self.session = session or requests.Session()

    def post(self, path, payload):
        response = self.session.post(TELNYX_API + path, json=payload, timeout=20,
                                     headers={"Authorization": f"Bearer {self.api_key}"})
        if response.status_code >= 300:
            # Never log credentials, numbers or the raw provider response.
            raise RuntimeError(f"Telnyx HTTP {response.status_code} on {path.split('/')[-1]}")
        return response.json().get("data", {})

    def dial(self, destination, public_url, token):
        host = public_url.split("://", 1)[-1].rstrip("/")
        return self.post("/calls", {
            "connection_id": self.connection_id, "from": self.source, "to": destination,
            "command_id": str(uuid4()),
            "timeout_secs": RING_SECONDS, "retry_on_timeout": False,
            "time_limit_secs": CALL_LIMIT_SECONDS,
            "webhook_url": f"https://{host}/webhooks/telnyx",
            "answering_machine_detection": "detect",
            "stream_url": f"wss://{host}/media/{token}",
            "stream_track": "inbound_track",  # the callee's voice
            "stream_codec": "PCMU",
            "stream_bidirectional_mode": "rtp",
            "stream_bidirectional_codec": "PCMU",
        })

    def hangup(self, call_control_id):
        self.post(f"/calls/{call_control_id}/actions/hangup", {"command_id": str(uuid4())})


class PhoneServer:
    """Webhook + media WebSocket server on 127.0.0.1, running in a background thread."""

    def __init__(self, calls, public_url, public_key, port=8766, loop=None):
        self.calls, self.public_url, self.port = calls, public_url.rstrip("/"), port
        self.receiver = EventReceiver(public_key, on_event=self.on_event)
        self.events, self.speaker = queue.Queue(), queue.Queue()
        self.token = None
        self.socket = None
        self.loop = loop or asyncio.new_event_loop()
        if loop is not None:
            return  # Cloud service mounts webhook/media on its existing HTTP server.
        started = threading.Event()
        threading.Thread(target=self.serve, args=(started,), daemon=True).start()
        if not started.wait(10):
            raise RuntimeError("The phone server did not start")

    def serve(self, started):
        asyncio.set_event_loop(self.loop)
        app = web.Application(client_max_size=65536)
        app.add_routes([web.post("/webhooks/telnyx", self.webhook),
                        web.get("/media/{token}", self.media),
                        web.get("/healthz", lambda request: web.json_response({"ok": True}))])
        runner = web.AppRunner(app, access_log=None)
        self.loop.run_until_complete(runner.setup())
        self.loop.run_until_complete(web.TCPSite(runner, "127.0.0.1", self.port).start())
        started.set()
        self.loop.run_forever()

    def on_event(self, event_type, payload):
        self.events.put((event_type, payload))

    async def webhook(self, request):
        status, body = self.receiver.receive(await request.read(),
                                             request.headers.get("telnyx-signature-ed25519", ""),
                                             request.headers.get("telnyx-timestamp", ""))
        return web.json_response(body, status=status)

    async def media(self, request):
        # The per-call random token in the URL authorizes exactly one stream.
        token = self.token
        if not token or not hmac.compare_digest(request.match_info["token"], token) or self.socket:
            return web.json_response({"error": "Unknown stream"}, status=403)
        socket = web.WebSocketResponse(max_msg_size=1 << 20)
        await socket.prepare(request)
        self.socket = socket
        log.info("Call audio stream connected")
        try:
            async for message in socket:
                if message.type != web.WSMsgType.TEXT:
                    continue
                event = json.loads(message.data)
                kind = event.get("event")
                if kind == "media" and event["media"].get("track", "inbound").startswith("inbound"):
                    self.speaker.put(from_phone(base64.b64decode(event["media"]["payload"])))
                elif kind == "start":
                    log.info("Call audio format: %s", json.dumps(event["start"].get("media_format")))
                elif kind == "error":
                    log.error("Call audio error: %s", json.dumps(event.get("payload")))
                elif kind == "stop":
                    break
        finally:
            self.socket = None
            log.info("Call audio stream closed")
        return socket

    def send_to_phone(self, pcm16k):
        socket = self.socket
        if socket is None or socket.closed:
            return
        message = json.dumps({"event": "media",
                              "media": {"payload": base64.b64encode(to_phone(pcm16k)).decode()}})
        asyncio.run_coroutine_threadsafe(socket.send_str(message), self.loop)

    def call(self, link, destination):
        """Dials, bridges the ESP32 with the callee until hangup, and returns the outcome.

        Outcomes: "ended" (a person answered), "no_answer", "machine" (voicemail),
        "cancelled" (hang-up button before an answer) or "failed".
        """
        self.events, self.speaker = queue.Queue(), queue.Queue()
        self.token = secrets.token_urlsafe(24)
        try:
            call_id = self.calls.dial(destination, self.public_url, self.token)["call_control_id"]
        except Exception as error:
            self.token = None
            log.error("Dial failed: %s", error)
            return "failed"
        log.info("Dialing (call control %s)", call_id)
        outcome, answered_at, person, hangup_sent = None, None, False, None
        started = time.monotonic()
        got_event = False
        mic = bytearray()

        def hang_up(result):
            nonlocal outcome, hangup_sent
            if hangup_sent is None:
                outcome, hangup_sent = outcome or result, time.monotonic()
                try:
                    self.calls.hangup(call_id)
                except Exception as error:
                    log.error("Hangup failed: %s. Check the Telnyx portal.", error)
        try:
            link.call_mode(True)
            while True:
                now = time.monotonic()
                while not self.events.empty():
                    kind, payload = self.events.get()
                    if payload.get("call_control_id") != call_id:
                        continue
                    got_event = True
                    if kind == "call.answered":
                        answered_at = now
                        log.info("Call answered; checking for voicemail")
                    elif kind == "call.machine.detection.ended":
                        result = payload.get("result")
                        log.info("Voicemail check: %s", result)
                        if result == "machine":
                            hang_up("machine")
                        elif not person:
                            person = True  # "human" or "not_sure" (Telnyx: treat as human)
                            log.info("Connected: microphone -> phone, phone -> speaker")
                    elif kind == "call.hangup":
                        log.info("Call ended: %s (%s)", payload.get("hangup_cause"), payload.get("hangup_source"))
                        return outcome or ("ended" if answered_at else "no_answer")
                if answered_at and not person and hangup_sent is None and now - answered_at > AMD_FALLBACK_SECONDS:
                    person = True
                    log.info("No voicemail result; connecting as a person")
                if hangup_sent is None and not got_event and now - started > EVENT_TIMEOUT:
                    log.error("No call events received; is the public URL reachable?")
                    hang_up("failed")
                if hangup_sent is None and not answered_at and now - started > RING_GUARD:
                    hang_up("no_answer")
                if hangup_sent and now - hangup_sent > HANGUP_WAIT:
                    log.error("No hangup event; check the Telnyx portal")
                    return outcome

                while not self.speaker.empty():
                    link.stream(self.speaker.get())
                frame = link.poll(0.01)
                if not frame:
                    continue
                kind, payload = frame
                if kind == "M" and person and hangup_sent is None:
                    mic.extend(payload)  # call-mode voice is already mono
                    while len(mic) >= PHONE_FRAME_BYTES:
                        self.send_to_phone(bytes(mic[:PHONE_FRAME_BYTES]))
                        del mic[:PHONE_FRAME_BYTES]
                elif kind == "H":
                    log.info("Hang-up button pressed")
                    hang_up("ended" if answered_at else "cancelled")
                elif kind == "I" and payload == READY_MESSAGE:
                    log.error("The ESP32 restarted during the call")
                    hang_up("failed")
        except (ConnectionError, TimeoutError):
            hang_up("failed")  # A disconnected gadget must not leave a known call live.
            raise
        finally:
            self.token = None
            try:
                link.call_mode(False)
            except (ConnectionError, TimeoutError):
                pass
