"""Standalone Confio judge demo. Run with python web/demo/server.py."""
import argparse
import asyncio
import base64
from collections import deque
import json
from html import escape
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

from aiohttp import web
from dotenv import load_dotenv
import requests

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
from voice_agent import (Agent, DEFAULT_VOICE_ID, SYSTEM_PROMPT_PATH, answer,
                         synthesize, to_wav, transcribe)
from photo_tools import PhotoTools, jpeg


class DemoProviders:
    """Local storage receipts and a phone inbox. No network client for these services."""
    def __init__(self):
        self.events = []

    def post(self, url, **kwargs):
        payload = kwargs.get("json", {})
        body = {}
        if url == "https://api.telnyx.com/v2/messages":
            self.events.append({"kind": "sms", "text": payload["text"]})
            body = {"data": {"id": uuid4().hex, "to": [{"status": "delivered"}]}}
        elif "/object/sign/" in url:
            body = {"signedURL": "/demo-photo/" + url.split("/")[-2]}
        elif not url.startswith("https://demo.invalid/storage/v1/object/"):
            raise ValueError("Unexpected demo provider")
        return SimpleNamespace(status_code=200, json=lambda: body)


def sample_image():
    path = os.getenv("CONFIO_DEMO_SAMPLE_OBJECT")
    if not path:
        return (ROOT / "media/camera-test-2.jpg").read_bytes()
    base = os.environ["SUPABASE_URL"].rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("SUPABASE_URL must use HTTPS")
    key = os.environ["SUPABASE_SECRET_KEY"]
    bucket = quote(os.environ["SUPABASE_STORAGE_BUCKET"], safe="")
    response = requests.get(f"{base}/storage/v1/object/authenticated/{bucket}/{quote(path)}",
                            headers={"apikey": key, "Authorization": "Bearer " + key},
                            timeout=15, allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError(f"Demo sample download failed (HTTP {response.status_code})")
    jpeg(response.content)  # Validate the JPEG without changing the original bytes.
    return response.content


class DemoSession:
    def __init__(self, sample):
        self.folder = tempfile.TemporaryDirectory(prefix="confio-demo-")
        self.providers = DemoProviders()
        self.touched = time.monotonic()
        self.busy = False
        self.turn_count = 0
        self.photo_count = 0
        self.call_state = "idle"
        directory = Path(self.folder.name)
        config = {"SUPABASE_URL": "https://demo.invalid", "SUPABASE_SECRET_KEY": "demo",
                  "SUPABASE_STORAGE_BUCKET": "photos", "TELNYX_API_KEY": "demo",
                  "TELNYX_MESSAGING_PROFILE_ID": "demo", "TELNYX_SMS_SENDER": "Confio"}
        contacts = {"Juan": "+15005550006"}
        key = os.environ["META_API_KEY"]
        self.photos = PhotoTools(config, key, directory, contacts,
                                 session=self.providers, capture=lambda: sample)
        prompt = SYSTEM_PROMPT_PATH.read_text() + (
            "\nThis is a browser demo. Calls and SMS delivery are simulated. "
            "Tell the user when an action is simulated.")
        self.agent = Agent(key, os.environ["CARTESIA_API_KEY"], DEFAULT_VOICE_ID,
                           prompt, directory, contacts=contacts, photos=self.photos)

    def turn(self, text, audio, webcam=None):
        agent = self.agent
        if audio:
            text = transcribe(agent.session, agent.meta_key, audio)
        if not text:
            return {"error": "No speech detected."}
        self.providers.events.clear()
        agent.begin_turn()
        agent.pending_call = None

        restored_photo_id = None

        def run(name, arguments):
            nonlocal restored_photo_id
            if name == "take_photo" and os.getenv("CONFIO_DEMO_PUBLIC") == "1" and agent.capture_result is None:
                if self.photo_count >= 3:
                    return {"error": "This demo session has reached its photo limit."}
                self.photo_count += 1
            capture = self.photos.capture
            if name == "take_photo" and webcam is not None:
                self.photos.capture = lambda: webcam
            try:
                result = agent.run_tool(name, arguments)
            finally:
                self.photos.capture = capture
            if name == "call_contact" and agent.pending_call:
                self.call_state = "ringing"
                self.providers.events.append({"kind": "call", "text": agent.pending_call[0]})
                agent.pending_call = None
                return {**result, "simulated": True}
            if name == "take_photo" and result.get("status") == "restored":
                restored_photo_id = result["photo_id"]
                self.photos.photos[restored_photo_id]["webcam"] = webcam is not None
            if name in ("take_photo", "save_photo", "send_sms") and "error" not in result:
                self.providers.events.append({"kind": name, "text": result["status"]})
            return result

        start = len(agent.history)
        reply = answer(agent.client, agent.system_prompt, text, tools=agent.tools,
                       run=run, history=agent.history, max_rounds=6)
        searches = sum(item.get("type") == "web_search_call" for item in agent.history[start:])
        for event in self.providers.events:
            if event["kind"] == "sms":
                text, separator, photo_id = event["text"].rpartition(" https://demo.invalid/demo-photo/")
                if separator and photo_id in self.photos.photos:
                    event["text"] = text
                    photo = self.photos.photos[photo_id]
                    event["image"] = self.restored_image(photo)
                    event["webcam"] = photo.get("webcam", False)
        result = {"transcript": text, "reply": reply, "events": list(self.providers.events),
                  "searches": searches}
        if restored_photo_id is not None:
            result["restored"] = self.restored_image(self.photos.photos[restored_photo_id])
            if webcam is not None:
                result["webcam_original"] = base64.b64encode(webcam).decode()
        if reply:
            try:
                pcm = synthesize(agent.session, agent.cartesia_key, agent.voice_id, reply)
                result["audio"] = base64.b64encode(to_wav(pcm)).decode()
            except (RuntimeError, requests.RequestException):
                result["audio_error"] = True
        agent.mark_idle()
        return result


    @staticmethod
    def restored_image(photo):
        return base64.b64encode((photo["folder"] / "restored.jpg").read_bytes()).decode()


def create_app(sample):
    sessions = {}
    recent_turns = deque()
    public = os.getenv("CONFIO_DEMO_PUBLIC") == "1"
    app = web.Application(client_max_size=2 * 1024 * 1024)

    async def turn(request):
        data = await request.read()
        now = time.monotonic()
        if public:
            while recent_turns and now - recent_turns[0] >= 3600:
                recent_turns.popleft()
            if len(recent_turns) >= 60 or sum(session.busy for session in sessions.values()) >= 2:
                raise web.HTTPTooManyRequests(text="The demo is at capacity. Try again later.")
        for key, session in list(sessions.items()):
            if not session.busy and now - session.touched > 300:
                sessions.pop(key).folder.cleanup()
        key = request.cookies.get("confio_demo")
        state = sessions.get(key)
        if state is None:
            if len(sessions) >= 8:
                raise web.HTTPServiceUnavailable(text="Demo is busy. Try again shortly.")
            key = uuid4().hex
            state = sessions[key] = DemoSession(sample)
        if state.busy or now - state.touched < 1 and state.agent.history:
            raise web.HTTPTooManyRequests()
        audio = data if request.content_type == "audio/wav" else None
        webcam = None
        if request.content_type == "image/jpeg":
            try:
                webcam = jpeg(data)
            except (OSError, ValueError):
                raise web.HTTPBadRequest(text="Invalid webcam image") from None
        if webcam is not None:
            text = "I have aimed my camera and I am ready. Take the photo now."
        else:
            text = "" if audio else data.decode("utf-8", errors="replace").strip()
        if audio is None and not 0 < len(text) <= 500:
            raise web.HTTPBadRequest(text="Enter a short request.")
        if public:
            if state.turn_count >= 15:
                raise web.HTTPTooManyRequests(text="This demo session has reached its turn limit.")
            state.turn_count += 1
            recent_turns.append(now)
        state.busy = True
        try:
            result = await asyncio.to_thread(state.turn, text, audio, webcam)
        except Exception as error:
            print("Demo turn failed:", type(error).__name__, file=sys.stderr)
            result = {"error": "A demo service did not respond. Please try again."}
        finally:
            state.busy = False
            state.touched = time.monotonic()
        response = web.json_response(result)
        response.set_cookie("confio_demo", key, httponly=True, samesite="Strict", secure=public or request.secure)
        return response

    async def accept_call(request):
        state = sessions.get(request.cookies.get("confio_demo"))
        accepted = bool(state and state.call_state == "ringing")
        if accepted:
            state.call_state = "connected"
        return web.json_response({"accepted": accepted})

    async def cancel(request):
        state = sessions.get(request.cookies.get("confio_demo"))
        active = bool(state and state.call_state != "idle")
        if active:
            state.call_state = "idle"
        return web.json_response({"cancelled": active})

    async def index(_):
        html = (HERE / "index.html").read_text()
        caller_id = os.getenv("CONFIO_DEMO_CALLER_ID", "+1 (202) 555-0142")
        html = html.replace("{{DEMO_CALLER_ID}}", escape(caller_id))
        if os.getenv("CONFIO_DEMO_SAMPLE_ROTATE_180") == "1":
            html = html.replace("<body>", '<body class="rotate-sample">')
        return web.Response(text=html, content_type="text/html")

    async def original(_):
        return web.Response(body=sample, content_type="image/jpeg")

    async def call_style(_):
        return web.FileResponse(ROOT / "web/call/styles.css")

    app.router.add_get("/call-style.css", call_style)
    app.router.add_get("/", index)
    app.router.add_get("/sample.jpg", original)
    app.router.add_post("/turn", turn)
    app.router.add_post("/accept-call", accept_call)
    app.router.add_post("/cancel", cancel)
    app.router.add_static("/assets", HERE / "assets")
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--public", action="store_true")
    args = parser.parse_args()
    if args.public:
        os.environ["CONFIO_DEMO_PUBLIC"] = "1"
    else:
        load_dotenv(ROOT / ".env")
    if not all(os.getenv(key) for key in ("META_API_KEY", "CARTESIA_API_KEY")):
        raise SystemExit("Set META_API_KEY and CARTESIA_API_KEY in your environment or ignored .env.")
    web.run_app(create_app(sample_image()), host="0.0.0.0" if args.public else "127.0.0.1", port=8080)
