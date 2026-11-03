"""Camera -> Muse Image -> private Supabase Storage -> Telnyx SMS.

Only opaque photo IDs enter the model context. Keys, phone numbers and signed
URLs stay in this module. No automatic retry of image generation or SMS sends.
"""
import base64
import binascii
import io
import json
import logging
import time
import unicodedata
from urllib.parse import quote, urljoin, urlsplit
from uuid import uuid4

import requests
from openai import OpenAI, OpenAIError
from PIL import Image, UnidentifiedImageError

log = logging.getLogger("voice_agent")
RESTORE_PROMPT = (
    "This photo was taken with a small low-resolution ESP32 camera. It may have a strong green or yellow "
    "color cast, horizontal line artifacts, noise and blur. Recreate it as the same moment photographed with "
    "a modern high-resolution smartphone: remove the color cast and line artifacts, use natural true-to-life "
    "colors and balanced exposure, and render sharp, clean detail. Keep the same scene, framing, people, poses, "
    "objects and text. Do not add or remove anything, and do not apply artistic styles or filters."
)
# Chosen in a side-by-side test: low effort without tools was faster (7.5 s vs 15.7 s)
# and kept the face closer to the original than high effort, which invented details.
RESTORE_OPTIONS = {"reasoning_strength": "low", "tool_enablement": {
    "enable_image_search": False, "enable_web_search": False, "enable_shell": False}}
PHOTO_ENV = ("CAMERA_URL", "SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_STORAGE_BUCKET",
             "TELNYX_API_KEY", "TELNYX_MESSAGING_PROFILE_ID", "TELNYX_SMS_SENDER")


def tool(name, description, properties=None, required=None):
    return {"type": "function", "name": name, "description": description,
            "parameters": {"type": "object", "properties": properties or {},
                           "required": required or [], "additionalProperties": False}}


def photo_tools(names):
    return [
        tool("take_photo", "Capture NOW from the handheld camera and restore with Muse Image. "
             "Only call when the user says they are ready to take it; when they first ask to "
             "prepare a photo, ask them to aim and say when ready. Returns a photo_id."),
        tool("save_photo", "Save the captured original and restored photo to Supabase Storage. "
             "Required before sending. Repeated calls reuse the saved photo.",
             {"photo_id": {"type": "string"}}, ["photo_id"]),
        tool("send_sms", "Send one SMS to a saved contact, with the user's exact text, "
             "a saved photo link, or both. Include at least one of text and photo_id. "
             "Only call when the user asks to send. Saved contacts: " + ", ".join(names) + ".",
             {"contact": {"type": "string"}, "text": {"type": "string"}, "photo_id": {"type": "string"}},
             ["contact"]),
    ]


def name_key(value):
    return " ".join(unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower().split())


def jpeg(data):
    if not data or len(data) > 25 * 1024 * 1024:
        raise ValueError("Invalid image size")
    with Image.open(io.BytesIO(data)) as image:
        if image.width * image.height > 20_000_000:
            raise ValueError("Image dimensions exceed limit")
        out = io.BytesIO()
        image.convert("RGB").save(out, format="JPEG", quality=95)
        return out.getvalue()


class PhotoTools:
    def __init__(self, config, meta_key, directory, contacts, session=None, image_client=None, capture=None):
        self.config = config
        self.directory = directory
        self.contacts = contacts
        self.session = session or requests.Session()
        self.image_client = image_client or OpenAI(
            base_url="https://api.meta.ai/v1", api_key=meta_key, timeout=180, max_retries=0)
        self.photos = {}
        self.text_messages = {}
        self.capture = capture
        camera = urlsplit(config.get("CAMERA_URL", ""))
        storage = urlsplit(config["SUPABASE_URL"])
        if capture is None and (camera.scheme not in ("http", "https") or not camera.hostname or camera.username or camera.password):
            raise ValueError("CAMERA_URL must be the camera HTTP address without credentials")
        if storage.scheme != "https" or not storage.hostname or storage.username or storage.password:
            raise ValueError("SUPABASE_URL must be an HTTPS project URL without credentials")
        self.storage_url = config["SUPABASE_URL"].rstrip("/")
        self.headers = {"apikey": config["SUPABASE_SECRET_KEY"]}
        # Legacy service_role JWTs also need bearer authentication.
        if not config["SUPABASE_SECRET_KEY"].startswith("sb_secret_"):
            self.headers["Authorization"] = "Bearer " + config["SUPABASE_SECRET_KEY"]

    def reset(self):
        self.photos.clear()
        self.text_messages.clear()

    def request(self, method, url, **kwargs):
        response = getattr(self.session, method)(url, timeout=30, allow_redirects=False, **kwargs)
        if not 200 <= response.status_code < 300:
            # Response bodies/URLs can contain signed links, keys, or contact numbers.
            raise ValueError(f"Provider HTTP {response.status_code}")
        return response

    def run(self, name, arguments):
        try:
            args = json.loads(arguments or "{}")
            required, optional = {
                "take_photo": (set(), set()),
                "save_photo": ({"photo_id"}, set()),
                "send_sms": ({"contact"}, {"text", "photo_id"}),
            }[name]
            if not isinstance(args, dict) or not required <= set(args) <= required | optional or any(
                    not isinstance(v, str) or not v.strip() for v in args.values()):
                return {"error": "Invalid tool arguments"}
            if name == "take_photo":
                return self.take_photo()
            photo_id = args.get("photo_id")
            photo = self.photos.get(photo_id)
            if photo_id is not None and photo is None:
                return {"error": "Unknown photo_id. Use the ID returned by take_photo."}
            if name == "save_photo":
                return self.save_photo(photo)
            if photo is not None:
                return self.send_photo_sms(photo, args["contact"], args.get("text"))
            if "text" not in args:
                return {"error": "Provide text, a saved photo_id, or both."}
            return self.send_text_sms(args["contact"], args["text"])
        except (requests.RequestException, OpenAIError, ValueError, KeyError, TypeError,
                AttributeError, UnidentifiedImageError, OSError, binascii.Error) as error:
            log.warning("Photo tool %s failed (%s)", name, type(error).__name__)
            return {"error": f"{name} failed ({type(error).__name__}). Do not claim success. "
                    "Do not automatically repeat capture or SMS; tell the user the step failed."}

    def take_photo(self):
        photo_id = uuid4().hex
        original = b""
        # Existing camera firmware queues up to two frames while idle. Drain them
        # before selecting the new frame, otherwise a handheld shot can be old.
        if self.capture is not None:
            original = self.capture()  # WiFi camera drains stale buffers before sending.
        else:
            for _ in range(3):
                original = self.request("get", self.config["CAMERA_URL"].rstrip("/") + "/photo").content
        jpeg(original)  # validate; preserve the camera's original bytes
        folder = self.directory / photo_id
        folder.mkdir(parents=True)
        (folder / "original.jpg").write_bytes(original)
        started = time.monotonic()
        result = self.image_client.images.edit(model="muse-image-1.0", prompt=RESTORE_PROMPT,
                    image=("original.jpg", original, "image/jpeg"), n=1, response_format="b64_json",
                    extra_body=RESTORE_OPTIONS)
        if not result.data or not result.data[0].b64_json:
            raise ValueError("Muse Image returned no image")
        restored = jpeg(base64.b64decode(result.data[0].b64_json, validate=True))
        (folder / "restored.jpg").write_bytes(restored)
        self.photos[photo_id] = {"id": photo_id, "folder": folder, "saved": False, "uploaded": set(), "sms": {}}
        log.info("Photo %s restored with Muse Image in %.2f s", photo_id, time.monotonic() - started)
        return {"photo_id": photo_id, "status": "restored", "next": "save_photo"}

    def save_photo(self, photo):
        bucket = quote(self.config["SUPABASE_STORAGE_BUCKET"], safe="")
        for variant in ("original", "restored"):
            if variant in photo["uploaded"]:
                continue
            path = f"{bucket}/{photo['id']}/{variant}.jpg"
            self.request("post", self.storage_url + "/storage/v1/object/" + path,
                         headers={**self.headers, "Content-Type": "image/jpeg", "x-upsert": "true"},
                         data=(photo["folder"] / f"{variant}.jpg").read_bytes())
            photo["uploaded"].add(variant)
        photo["saved"] = True
        log.info("Photo %s saved to Supabase (original + restored)", photo["id"])
        return {"photo_id": photo["id"], "status": "saved", "next": "send_sms when recipient and message choice are clear"}

    def match_contact(self, contact):
        return next((name for name in self.contacts if name_key(name) == name_key(contact)), None)

    def send_text_sms(self, contact, text):
        match = self.match_contact(contact)
        if not match:
            return {"error": "Unknown saved contact", "saved_contacts": list(self.contacts)}
        key = (match, text)
        if key in self.text_messages:
            return dict(self.text_messages[key])
        result = self.text_messages[key] = {"contact": match, "status": "unknown"}
        return self.post_sms(result, text)

    def send_photo_sms(self, photo, contact, text=None):
        match = self.match_contact(contact)
        if not match:
            return {"error": "Unknown saved contact", "saved_contacts": list(self.contacts)}
        if match in photo["sms"]:
            return dict(photo["sms"][match])
        if not photo["saved"]:
            return {"error": "Photo is not saved. Call save_photo first."}
        bucket = quote(self.config["SUPABASE_STORAGE_BUCKET"], safe="")
        signed = self.request("post", self.storage_url + f"/storage/v1/object/sign/{bucket}/{photo['id']}/restored.jpg",
                              headers=self.headers, json={"expiresIn": 604800}).json()
        path = signed.get("signedURL") or signed.get("signedUrl")
        if not isinstance(path, str) or not path:
            raise ValueError("Missing signed photo URL")
        link = urljoin(self.storage_url + "/storage/v1/", path.lstrip("/") if path.startswith("/object/") else path)
        if urlsplit(link).netloc != urlsplit(self.storage_url).netloc or urlsplit(link).scheme != "https":
            raise ValueError("Invalid signed photo URL")
        # Mark before POST: an ambiguous timeout must not cause a duplicate send.
        result = photo["sms"][match] = {"contact": match, "status": "unknown", "photo_id": photo["id"]}
        caption = text if text is not None else "Confio: te compartieron una foto."
        return self.post_sms(result, caption + " " + link)

    def post_sms(self, result, text):
        data = self.request("post", "https://api.telnyx.com/v2/messages",
            headers={"Authorization": "Bearer " + self.config["TELNYX_API_KEY"]},
            json={"from": self.config["TELNYX_SMS_SENDER"], "to": self.contacts[result["contact"]],
                  "messaging_profile_id": self.config["TELNYX_MESSAGING_PROFILE_ID"],
                  "type": "SMS", "text": text}).json()["data"]
        result.update(self.message_status(data))
        log.info("SMS status=%s", result["status"])
        return dict(result)

    @staticmethod
    def message_status(data):
        status = ((data.get("to") or [{}])[0].get("status") or "unknown")
        if data.get("errors"):
            status = "failed"
        return {"message_id": data["id"], "status": status,
                "delivered": status == "delivered", "parts": data.get("parts")}

    def sms_status(self, result):
        if result.get("message_id") and result["status"] not in ("delivered", "failed", "sending_failed", "delivery_failed"):
            data = self.request("get", "https://api.telnyx.com/v2/messages/" + quote(result["message_id"], safe=""),
                headers={"Authorization": "Bearer " + self.config["TELNYX_API_KEY"]}).json()["data"]
            result.update(self.message_status(data))
        return dict(result)

    def refresh_sms(self):
        """Refresh pending delivery on the next user turn; never sends a message."""
        updates = []
        for previous in [*self.text_messages.values(),
                         *(result for photo in self.photos.values() for result in photo["sms"].values())]:
            if previous["status"] in ("delivered", "failed", "sending_failed", "delivery_failed", "unknown"):
                continue
            old = dict(previous)
            try:
                current = self.sms_status(previous)
                if current != old:
                    updates.append(current)
            except (requests.RequestException, ValueError, KeyError, TypeError):
                log.warning("Could not refresh SMS delivery")
        return updates
