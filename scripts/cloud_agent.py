"""One-device cloud gateway. The existing Agent/tools remain the source of behavior.

The asyncio loop owns sockets; one worker owns the synchronous Agent and call
loop. No provider request blocks camera, microphone, hang-up or webhook input.
"""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import hmac
import json
import logging
import os
from pathlib import Path
import queue
import tempfile
import threading
import time
from uuid import uuid4

from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from voice_agent import (Agent, CALL_OUTCOMES, CHAT_MODEL, DEFAULT_VOICE_ID,
                         REASONING_EFFORT, SYSTEM_PROMPT_PATH)
from voice_link import (HEADER, MIC_MAGIC, MIC_TYPES, MAX_MIC_PAYLOAD,
                        PLAY_CHUNK_BYTES, SAMPLE_RATE, mono_channel, pack)
from phone_call import PhoneServer, TelnyxCalls
from photo_tools import PhotoTools, PHOTO_ENV
from telnyx_call import phone_number

log = logging.getLogger("voice_agent")
MAX_RECORD_SECONDS = 120
WINDOW = 16  # 512 ms at 16 kHz; bounded on both ends
REQUIRED = ("META_API_KEY", "CARTESIA_API_KEY", "TELNYX_API_KEY", "TELNYX_CONNECTION_ID",
            "TELNYX_FROM_NUMBER", "TELNYX_PUBLIC_KEY", "CONTACTS_JSON", "CONFIO_DEVICE_TOKEN",
            "CONFIO_CAMERA_TOKEN", "CONFIO_ADMIN_TOKEN") + tuple(k for k in PHOTO_ENV if k != "CAMERA_URL")


class WifiVoiceLink:
    """The VoiceLink interface backed by one authenticated binary WebSocket."""
    def __init__(self, socket, loop):
        self.socket, self.loop = socket, loop
        self.frames = queue.Queue(maxsize=512)
        self.closed = threading.Event()
        self.condition = threading.Condition()
        self.sent = self.acked = 0
        self.record_started_at = None
        self.latest_press = None
        self.busy = False

    def close(self):
        self.closed.set()
        with self.condition:
            self.condition.notify_all()

    def feed(self, data):
        if len(data) < HEADER.size:
            raise ValueError("Short voice frame")
        magic, kind, size = HEADER.unpack_from(data)
        payload = data[HEADER.size:]
        if magic != MIC_MAGIC or kind not in MIC_TYPES or size != len(payload) or size > MAX_MIC_PAYLOAD:
            raise ValueError("Invalid voice frame")
        kind = chr(kind)
        if kind in "DFMSAH" and (len(payload) % (4 if kind == "D" else 2)):
            raise ValueError("Misaligned audio")
        if kind in "SFAH" and payload:
            raise ValueError("Control frames must be empty")
        received = time.monotonic()
        if kind == "S":
            self.latest_press = received
            self.busy = True
        if kind == "A":
            with self.condition:
                if self.acked < self.sent:
                    self.acked += 1
                    self.condition.notify_all()
            return
        try:
            self.frames.put_nowait((kind, payload, received))
        except queue.Full as error:
            raise ValueError("Voice queue overflow") from error

    def send(self, kind, payload=b"", paced=False, live=False):
        if self.closed.is_set():
            raise ConnectionError("Device disconnected")
        if paced:
            with self.condition:
                deadline = time.monotonic() + 5
                while self.sent - self.acked >= WINDOW:
                    if live:
                        return  # Drop obsolete call audio rather than grow latency.
                    if self.closed.is_set():
                        raise ConnectionError("Device disconnected")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Speaker acknowledgements stopped")
                    self.condition.wait(remaining)
                self.sent += 1
        future = asyncio.run_coroutine_threadsafe(self.socket.send_bytes(pack(kind, payload)), self.loop)
        try:
            future.result(timeout=5)
        except Exception as error:
            future.cancel()
            self.close()
            raise ConnectionError("Device send failed") from error

    def next_frame(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.closed.is_set():
            remaining = 0.1 if deadline is None else min(0.1, max(0, deadline - time.monotonic()))
            try:
                kind, payload, received = self.frames.get(timeout=remaining)
                if kind == "S":
                    self.record_started_at = received
                    self.busy = True
                return kind, payload
            except queue.Empty:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("No voice frame")
        raise ConnectionError("Device disconnected")

    def record(self):
        while self.next_frame()[0] != "S":
            pass
        stereo = bytearray()
        deadline = time.monotonic() + MAX_RECORD_SECONDS + 5
        while True:
            kind, payload = self.next_frame(timeout=max(0, deadline - time.monotonic()))
            if kind == "D":
                stereo.extend(payload)
                if len(stereo) > MAX_RECORD_SECONDS * SAMPLE_RATE * 4:
                    raise ConnectionError("Recording too long")
            elif kind == "F":
                self.busy = True
                self.send("B")  # WiFi-only busy control, no new user interaction.
                return mono_channel(stereo)

    def play(self, pcm):
        for i in range(0, len(pcm) - len(pcm) % 2, PLAY_CHUNK_BYTES):
            self.send("P", pcm[i:i + PLAY_CHUNK_BYTES], paced=True)
        self.send("E", paced=True)
        with self.condition:
            deadline = time.monotonic() + 8
            while self.acked < self.sent:
                if self.closed.is_set():
                    raise ConnectionError("Device disconnected")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Playback did not finish")
                self.condition.wait(remaining)

    def call_mode(self, on):
        if not on:
            self.send("E", paced=True)
        self.send("C" if on else "N")

    def stream(self, pcm):
        for i in range(0, len(pcm) - len(pcm) % 2, PLAY_CHUNK_BYTES):
            self.send("P", pcm[i:i + PLAY_CHUNK_BYTES], paced=True, live=True)

    def poll(self, timeout=0.02):
        try:
            return self.next_frame(timeout)
        except TimeoutError:
            return None


class CameraLink:
    """Correlates a single capture with its camera response; never retries a shot."""
    def __init__(self, loop):
        self.loop, self.socket = loop, None
        self.pending = None
        self.request_id = None

    async def capture_async(self):
        if self.socket is None or self.socket.closed:
            raise ValueError("Camera offline")
        if self.pending is not None:
            raise ValueError("Camera busy")
        self.pending = self.loop.create_future()
        self.request_id = uuid4().hex
        try:
            await self.socket.send_str("capture:" + self.request_id)
            return await asyncio.wait_for(self.pending, timeout=30)
        finally:
            self.pending, self.request_id = None, None

    def capture(self):
        future = asyncio.run_coroutine_threadsafe(self.capture_async(), self.loop)
        try:
            return future.result(timeout=35)
        except Exception as error:
            future.cancel()
            raise ValueError("Camera capture failed") from error

    def receive(self, data):
        # 32 ASCII hex request ID, followed by JPEG. Ignore late/unknown responses.
        if self.pending is not None and not self.pending.done() and data[:32] == self.request_id.encode():
            if len(data) <= 32 or len(data) > 1_500_000:
                self.pending.set_exception(ValueError("Invalid camera image"))
            else:
                self.pending.set_result(data[32:])

    def disconnect(self):
        self.socket = None
        if self.pending is not None and not self.pending.done():
            self.pending.set_exception(ValueError("Camera disconnected"))


class CloudAgent:
    def __init__(self, config=None):
        self.config = dict(os.environ if config is None else config)
        missing = [k for k in dict.fromkeys(REQUIRED) if not self.config.get(k, "").strip()]
        if missing:
            raise ValueError("Missing Modal Secrets: " + ", ".join(missing))
        tokens = [self.config[k] for k in ("CONFIO_DEVICE_TOKEN", "CONFIO_CAMERA_TOKEN", "CONFIO_ADMIN_TOKEN")]
        if any(len(t) < 32 for t in tokens) or len(set(tokens)) != 3:
            raise ValueError("Use three distinct random tokens of at least 32 characters")
        contacts = json.loads(self.config["CONTACTS_JSON"])
        if not isinstance(contacts, dict) or not contacts or any(not isinstance(n, str) or not n.strip() for n in contacts):
            raise ValueError("CONTACTS_JSON must map saved names to numbers")
        self.contacts = {name: phone_number(number) for name, number in contacts.items()}
        self.public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(self.config["TELNYX_PUBLIC_KEY"], validate=True))
        self.device = self.worker = self.phone = self.agent = self.camera = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="confio-agent")
        self.directory = tempfile.TemporaryDirectory(prefix="confio-")
        self.draining = False
        self.public_url = None
        self.interaction_active = False
        self.voice_lock = asyncio.Lock()
        self.camera_lock = asyncio.Lock()

    def authorized(self, request, key):
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + self.config[key]).encode()):
            raise web.HTTPUnauthorized(text="Unauthorized")

    def setup_agent(self, public_url, loop):
        if self.agent is not None:
            return
        self.public_url = public_url
        self.camera = CameraLink(loop)
        calls = TelnyxCalls(self.config["TELNYX_API_KEY"], self.config["TELNYX_CONNECTION_ID"],
                            phone_number(self.config["TELNYX_FROM_NUMBER"]))
        self.phone = PhoneServer(calls, public_url, self.public_key, loop=loop)
        folder = Path(self.directory.name)
        photos = PhotoTools(self.config, self.config["META_API_KEY"], folder / "photos",
                            self.contacts, capture=self.camera.capture)
        self.agent = Agent(self.config["META_API_KEY"], self.config["CARTESIA_API_KEY"],
                           self.config.get("CARTESIA_VOICE_ID", DEFAULT_VOICE_ID),
                           SYSTEM_PROMPT_PATH.read_text().strip(), folder,
                           self.config.get("CONFIO_MODEL", CHAT_MODEL),
                           self.config.get("CONFIO_EFFORT", REASONING_EFFORT), self.contacts, photos=photos)

    async def startup(self, app):
        # Set explicitly rather than inheriting a UTC container clock.
        os.environ["CONFIO_TIMEZONE"] = self.config.get("CONFIO_TIMEZONE", "America/Lima")

    def ensure_setup(self, request):
        # Use trusted deployment config, never an untrusted Host header for callbacks.
        public_url = self.config.get("CONFIO_PUBLIC_URL", "").rstrip("/")
        if not public_url.startswith("https://"):
            raise web.HTTPServiceUnavailable(text="Set CONFIO_PUBLIC_URL to the Modal HTTPS URL")
        self.setup_agent(public_url, asyncio.get_running_loop())

    async def health(self, request):
        return web.json_response({"ok": True, "service": "confio"})

    async def admin_status(self, request):
        self.authorized(request, "CONFIO_ADMIN_TOKEN")
        self.ensure_setup(request)
        return web.json_response({"busy": self.interaction_active or bool(self.device and self.device.busy),
                                  "draining": self.draining,
                                  "device_connected": self.device is not None,
                                  "camera_connected": self.camera.socket is not None,
                                  "tools": [tool.get("name", tool["type"]) for tool in self.agent.tools]})

    async def drain(self, request):
        self.authorized(request, "CONFIO_ADMIN_TOKEN")
        if self.interaction_active or (self.device and self.device.busy):
            raise web.HTTPConflict(text="User interaction active")
        self.draining = True
        if self.device:
            self.device.close()
            await self.device.socket.close(code=1012, message=b"Deploying")
        if self.camera and self.camera.socket is not None:
            await self.camera.socket.close(code=1012, message=b"Deploying")
        return web.json_response({"draining": True})

    async def undrain(self, request):
        self.authorized(request, "CONFIO_ADMIN_TOKEN")
        self.draining = False
        return web.json_response({"draining": False})

    async def voice(self, request):
        self.authorized(request, "CONFIO_DEVICE_TOKEN")
        self.ensure_setup(request)
        if self.draining or self.voice_lock.locked() or (self.worker is not None and not self.worker.done()):
            raise web.HTTPConflict(text="Device busy or deploying")
        async with self.voice_lock:
            return await self.serve_voice(request)

    async def serve_voice(self, request):
        socket = web.WebSocketResponse(max_msg_size=HEADER.size + MAX_MIC_PAYLOAD, heartbeat=20)
        await socket.prepare(request)
        if self.draining:
            await socket.close(code=1012)
            return socket
        link = WifiVoiceLink(socket, asyncio.get_running_loop())
        self.device = link
        self.worker = asyncio.get_running_loop().run_in_executor(self.executor, self.run, link)
        try:
            await socket.send_bytes(pack("N"))  # reset call/playback state on each connection
            async for message in socket:
                if message.type == web.WSMsgType.BINARY:
                    if self.draining:
                        break
                    # Mark busy at receipt, including recording, to guard deployment.
                    if message.data[:5] == b"MIC1S":
                        link.busy = True
                        self.interaction_active = True
                    link.feed(message.data)
                elif message.type == web.WSMsgType.ERROR:
                    break
        except ValueError:
            await socket.close(code=1008, message=b"Invalid frame")
        finally:
            link.close()
            await socket.close()
            if self.device is link:
                self.device = None
        return socket

    def run(self, link):
        while not link.closed.is_set():
            turn_started = False
            try:
                voice = link.record()
                turn_started = True
                self.interaction_active = True
                speech = self.agent.handle(voice, started_at=link.record_started_at)
                if link.closed.is_set():
                    raise ConnectionError("Device disconnected")
                if speech:
                    link.play(speech)
                if self.agent.pending_call:
                    name, number = self.agent.pending_call
                    self.agent.pending_call = None
                    outcome = "failed"
                    try:
                        outcome = self.phone.call(link, number) or "failed"
                    finally:
                        self.agent.remember_call_outcome(name, outcome)
                    announcement = CALL_OUTCOMES[outcome].format(name=name)
                    link.play(self.agent.say(announcement))
                    self.agent.history.append({"role": "assistant", "content": announcement})
                link.send("E", paced=True)  # release busy even for a short/empty turn
            except (ConnectionError, TimeoutError):
                link.close()
                asyncio.run_coroutine_threadsafe(link.socket.close(), link.loop)
                break
            except Exception as error:
                log.error("Cloud turn failed (%s)", type(error).__name__)  # no provider bodies/secrets
                try:
                    link.send("E", paced=True)
                except (ConnectionError, TimeoutError):
                    link.close()
            finally:
                if self.agent.pending_call:
                    self.agent.remember_call_outcome(self.agent.pending_call[0], "not_started")
                    self.agent.pending_call = None
                if turn_started:
                    self.agent.mark_idle()
                # Update deployment state on the socket loop, in order with new presses.
                link.loop.call_soon_threadsafe(self.finish_turn, link, link.record_started_at)

    def finish_turn(self, link, completed_press):
        if self.device is not None and self.device is not link:
            return
        link.busy = (not link.closed.is_set() and link.latest_press is not None and
                     (completed_press is None or link.latest_press > completed_press))
        self.interaction_active = link.busy

    async def camera_socket(self, request):
        self.authorized(request, "CONFIO_CAMERA_TOKEN")
        self.ensure_setup(request)
        if self.draining or self.camera_lock.locked():
            raise web.HTTPConflict(text="Camera busy or deploying")
        async with self.camera_lock:
            return await self.serve_camera(request)

    async def serve_camera(self, request):
        socket = web.WebSocketResponse(max_msg_size=1_500_000, heartbeat=20)
        await socket.prepare(request)
        if self.draining:
            await socket.close(code=1012)
            return socket
        self.camera.socket = socket
        try:
            async for message in socket:
                if message.type == web.WSMsgType.BINARY:
                    self.camera.receive(message.data)
                elif message.type == web.WSMsgType.TEXT and message.data.startswith("error:"):
                    if self.camera.pending is not None and not self.camera.pending.done():
                        self.camera.pending.set_exception(ValueError("Camera capture failed"))
        finally:
            self.camera.disconnect()
        return socket

    async def webhook(self, request):
        self.ensure_setup(request)
        return await self.phone.webhook(request)

    async def media(self, request):
        self.ensure_setup(request)
        return await self.phone.media(request)

    async def cleanup(self, app):
        self.draining = True
        if self.device:
            self.device.close()
            await self.device.socket.close(code=1001)
        if self.camera and self.camera.socket is not None:
            await self.camera.socket.close(code=1001)
        if self.worker is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self.worker), timeout=25)
            except (TimeoutError, asyncio.CancelledError):
                pass
        self.executor.shutdown(wait=False, cancel_futures=True)
        # Container-local audio/photos are transient; saved photos remain in Supabase.
        if self.worker is None or self.worker.done():
            self.directory.cleanup()

    def application(self):
        app = web.Application(client_max_size=65536)
        app.add_routes([web.get("/healthz", self.health), web.get("/device", self.voice),
                        web.get("/camera", self.camera_socket),
                        web.post("/webhooks/telnyx", self.webhook), web.get("/media/{token}", self.media),
                        web.get("/admin/status", self.admin_status), web.post("/admin/drain", self.drain),
                        web.post("/admin/undrain", self.undrain)])
        app.on_startup.append(self.startup)
        app.on_cleanup.append(self.cleanup)
        return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    web.run_app(CloudAgent().application(), host="0.0.0.0", port=8000, access_log=None)
