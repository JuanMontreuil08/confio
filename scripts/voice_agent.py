#!/usr/bin/env python3
"""Push-to-talk voice agent: ESP32 mic -> Meta transcribe -> Muse Spark -> Cartesia -> ESP32 speaker.

Usage (from the repository root, with the esp32/voice firmware on the board):
    python3 scripts/voice_agent.py --port /dev/cu.usbserial-0001

Reads META_API_KEY and CARTESIA_API_KEY from the environment or the root .env.
Every turn is logged to logs/voice-agent-<start time>.log, with the recorded
voice and the spoken answer saved as WAV files next to it. Conversation context
stays in RAM until five minutes of idle waiting have elapsed.
"""
import argparse
import array
import io
import json
import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import sys
import time
import unicodedata
import wave

import requests
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "esp32" / "voice"))
sys.path.insert(0, str(ROOT / "scripts"))
from voice_link import SAMPLE_RATE, VoiceLink, find_port  # noqa: E402

SYSTEM_PROMPT_PATH = ROOT / "prompts" / "assistant_system.md"
CONTACTS_PATH = ROOT / "contacts.json"  # ignored by git: {"Name": "+51999999999"}
PUBLIC_KEY_PATH = ROOT / ".telnyx-public-key"
LOG_DIR = ROOT / "logs"
META_BASE_URL = "https://api.meta.ai/v1"
TRANSCRIBE_URL = META_BASE_URL + "/asr/transcribe"
TRANSCRIBE_MODEL = "muse-voice-transcribe-1.0"
CHAT_MODEL = "muse-spark-1.3"  # --model muse-wonder-1.0 to compare
CARTESIA_URL = "https://api.cartesia.ai/tts/bytes"
CARTESIA_VERSION = "2026-08-14"
TTS_MODEL = "sonic-3.6"
DEFAULT_VOICE_ID = "b4b8e2af-6139-466e-a93a-30c20d2e1fc5"
MIN_RECORDING_SECONDS = 0.4
HTTP_TIMEOUT = 60
IDLE_SECONDS = 300

log = logging.getLogger("voice_agent")


def to_wav(mono_pcm):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(mono_pcm)
    return buffer.getvalue()


def transcribe(session, api_key, wav_bytes):
    """Sends one WAV to Muse Voice Transcribe and returns the transcript."""
    request = {"mode": "PUSH_TO_TALK", "model": TRANSCRIBE_MODEL,
               "audioEncoding": "WAV", "languageBias": ["spanish"]}
    response = session.post(
        TRANSCRIBE_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        files={"request": (None, json.dumps(request), "application/json"),
               "audio": ("voice.wav", wav_bytes, "audio/wav")},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Transcribe HTTP {response.status_code}: {response.text[:300]}")
    return (response.json().get("transcript") or "").strip()


def get_current_datetime(now=None):
    """Read the configured user timezone, or local laptop time in USB mode."""
    now = now or (datetime.now(ZoneInfo(os.environ["CONFIO_TIMEZONE"]))
                  if os.environ.get("CONFIO_TIMEZONE") else datetime.now().astimezone())
    return {"datetime": now.isoformat(timespec="minutes"), "weekday": now.strftime("%A")}


TOOL_FUNCTIONS = {"get_current_datetime": get_current_datetime}
TOOLS = [{
    "type": "function",
    "name": "get_current_datetime",
    "description": "Return the current local date, time and day of the week where the user lives. "
                   "Use it for any question about today's date, the day of the week or the time.",
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
}, {
    # Built-in Meta tool: the server runs the search; the model decides when to use it.
    "type": "web_search",
    "search_context_size": "medium",
}]
MAX_TOOL_ROUNDS = 3
MAX_SEARCHES = 2  # max_tool_calls counts built-in tool calls (web searches) per request
MAX_LOGGED_RESULTS = 5
REASONING_EFFORT = "medium"  # Spark rejects "none"; "low" is faster for direct answers


def run_tool(name, arguments):
    """Executes one requested tool; errors go back to the model instead of crashing the turn."""
    if name not in TOOL_FUNCTIONS:
        return {"error": f"Unknown tool {name}"}
    try:
        args = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {"error": "Arguments are not valid JSON"}
    if args != {}:  # get_current_datetime takes no arguments
        return {"error": "This tool takes no arguments"}
    return TOOL_FUNCTIONS[name]()


def field(obj, name):
    """Reads a field from an SDK object or a plain dict (older SDKs leave unknown parts as dicts)."""
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def log_searches(response):
    """Logs each web search query and its top results, so answers can be checked later.

    Meta returns url_citation annotations rarely; the opt-in web_search_call.results
    list (title and URL of every page the model considered) is the reliable source list.
    """
    for item in response.output:
        if item.type == "web_search_call":
            action = field(item, "action") or {}  # search (query), open_page or find_in_page (url)
            log.info("Web %s: %r", field(action, "type") or "search",
                     field(action, "query") or field(action, "url") or "")
            for result in (field(item, "results") or [])[:MAX_LOGGED_RESULTS]:
                log.info("  result: %s <%s>", field(result, "title"), field(result, "url"))
        elif item.type == "message":
            for block in getattr(item, "content", None) or []:
                for note in getattr(block, "annotations", None) or []:
                    if getattr(note, "type", "") == "url_citation":
                        log.info("  cited: %s <%s>", getattr(note, "title", ""), getattr(note, "url", ""))


def final_text(response):
    """Joins the answer messages, skipping "commentary" preambles such as "Voy a buscar..."."""
    texts = [block.text for item in response.output
             if item.type == "message" and getattr(item, "phase", None) != "commentary"
             for block in getattr(item, "content", None) or [] if getattr(block, "type", "") == "output_text"]
    return " ".join(texts) if texts else (response.output_text or "")


def spoken(text):
    """Removes links, URLs, citation marks and markdown that would be read aloud literally."""
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # [title](url) -> title
    text = re.sub(r"\(?https?://[^\s)]*[^\s).,;:!?]\)?", "", text)  # keep sentence punctuation
    text = re.sub(r"【[^】]*】|\[\d+\]", "", text)
    text = re.sub(r"[*_#`]+", "", text)
    return re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"\s{2,}", " ", text)).strip()


def answer(client, system_prompt, question, model=CHAT_MODEL, effort=REASONING_EFFORT,
           tools=TOOLS, run=run_tool, history=None, max_rounds=MAX_TOOL_ROUNDS):
    """Asks the Meta model (Responses API) for a short spoken answer, running any tools it requests.

    Stateless: each request resends the whole turn, including the model's encrypted
    reasoning, instead of using previous_response_id. In testing this cut the
    post-tool call from 10-16 s to 3-5 s and avoided "persistence service" 503s.
    """
    if history is None:
        history = []
    if not history:
        history.append({"role": "system", "content": system_prompt})
    history.append({"role": "user", "content": question})
    for round_number in range(max_rounds + 1):
        response = client.responses.create(
            model=model, input=list(history), tools=tools, reasoning={"effort": effort},
            store=False, include=["reasoning.encrypted_content", "web_search_call.results"],
            extra_body={"max_tool_calls": MAX_SEARCHES})  # older SDKs lack this argument
        log_searches(response)
        calls = [item for item in response.output if item.type == "function_call"]
        # Preserve ALL output, including the final answer and encrypted reasoning.
        history += [item.model_dump(exclude_none=True) for item in response.output]
        if not calls:
            return spoken(final_text(response))
        for call in calls:
            result = ({"error": "Tool round limit reached. No action was executed."}
                      if round_number == max_rounds else run(call.name, call.arguments))
            logged_args = "[SMS arguments omitted]" if call.name in ("send_sms", "send_photo_sms") else call.arguments
            log.info("Tool %s(%s) -> %s", call.name, logged_args, json.dumps(result))
            history.append({"type": "function_call_output", "call_id": call.call_id,
                            "output": json.dumps(result)})
        if round_number == max_rounds:
            raise RuntimeError(f"Model still requesting tools after {max_rounds} rounds")


def synthesize(session, api_key, voice_id, text):
    """Returns Cartesia speech as raw mono 16 kHz 16-bit PCM, ready for the ESP32."""
    response = session.post(
        CARTESIA_URL,
        headers={"Authorization": f"Bearer {api_key}", "Cartesia-Version": CARTESIA_VERSION},
        json={"model_id": TTS_MODEL, "transcript": text, "voice": {"id": voice_id},
              "language": "es",
              "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": SAMPLE_RATE}},
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Cartesia HTTP {response.status_code}: {response.text[:300]}")
    return response.content


def contact_key(name):
    """Case- and accent-insensitive contact name ("Náty " == "naty")."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(plain.lower().split())


def load_contacts(path=CONTACTS_PATH):
    from telnyx_call import phone_number
    contacts = json.loads(path.read_text())
    if not isinstance(contacts, dict) or not contacts:
        raise SystemExit(f"{path.name} must map names to numbers, like contacts.example.json")
    return {name: phone_number(number) for name, number in contacts.items()}


def call_tool(names):
    return {
        "type": "function",
        "name": "call_contact",
        "description": "Place a phone call to one of the user's saved contacts and connect them. "
                       "Saved contacts: " + ", ".join(names) + ".",
        "parameters": {"type": "object",
                       "properties": {"name": {"type": "string", "description": "The saved contact's name"}},
                       "required": ["name"], "additionalProperties": False},
    }


# Spoken by the agent after each call; {name} is the saved contact name.
CALL_OUTCOMES = {
    "ended": "La llamada con {name} terminó.",
    "no_answer": "{name} no contestó.",
    "machine": "{name} no contestó, respondió el buzón de voz.",
    "cancelled": "Listo, cancelé la llamada a {name}.",
    "failed": "No pude llamar a {name}. Inténtalo más tarde.",
}


class Agent:
    def __init__(self, meta_key, cartesia_key, voice_id, system_prompt, turn_dir, model=CHAT_MODEL,
                 effort=REASONING_EFFORT, contacts=None, photos=None, enable_calls=True):
        self.session = requests.Session()
        self.client = OpenAI(base_url=META_BASE_URL, api_key=meta_key, timeout=HTTP_TIMEOUT)
        self.meta_key = meta_key
        self.cartesia_key = cartesia_key
        self.voice_id = voice_id
        self.system_prompt = system_prompt
        self.turn_dir = turn_dir
        self.turn = 0
        self.model = model
        self.effort = effort
        self.contacts = contacts or {}
        self.photos = photos
        self.history = []
        self.idle_since = None
        self.capture_result = None
        self.tools = TOOLS + ([call_tool(list(self.contacts))] if self.contacts and enable_calls else [])
        if photos:
            from photo_tools import photo_tools
            self.tools += photo_tools(list(self.contacts))
        self.pending_call = None  # (name, number) chosen by the model during this turn

    def run_tool(self, name, arguments):
        if getattr(self, "photos", None) and name in ("take_photo", "save_photo", "send_photo_sms", "send_sms"):
            if name == "take_photo" and self.capture_result is not None:
                return self.capture_result
            result = self.photos.run(name, arguments)
            if name == "take_photo":
                self.capture_result = result  # one capture/restoration attempt per voice turn
            return result
        if name != "call_contact" or not self.contacts:
            return run_tool(name, arguments)
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return {"error": "Arguments are not valid JSON"}
        if set(args) != {"name"} or not isinstance(args["name"], str):
            return {"error": "Pass only the contact name"}
        if self.pending_call:
            return {"error": "A call is already being placed in this turn"}
        match = next((saved for saved in self.contacts if contact_key(saved) == contact_key(args["name"])), None)
        if not match:
            return {"error": f"No saved contact named {args['name']}", "saved_contacts": list(self.contacts)}
        # Only numbers from the saved contacts are ever dialed, never one from the transcript.
        self.pending_call = (match, self.contacts[match])
        return {"status": "calling", "contact": match}

    def say(self, text):
        """Speaks a fixed sentence (no model call), returning PCM for the speaker."""
        log.info("Saying %r", text)
        return synthesize(self.session, self.cartesia_key, self.voice_id, text)

    def begin_turn(self, started_at=None):
        started_at = time.monotonic() if started_at is None else started_at
        if self.idle_since is not None and started_at - self.idle_since >= IDLE_SECONDS:
            self.history.clear()
            if self.photos:
                self.photos.reset()
            log.info("Conversation cleared after five minutes idle")
        self.idle_since = None  # no expiry during recording, tools, playback or calls
        self.capture_result = None

    def mark_idle(self):
        self.idle_since = time.monotonic()

    def remember_call_outcome(self, name, outcome):
        self.history.append({"role": "developer", "content": "Actual phone call outcome: " +
                             json.dumps({"contact": name, "status": outcome})})

    def handle(self, voice_pcm, started_at=None):
        """Runs one turn and returns the PCM to play, or None when there is nothing to say."""
        self.turn += 1
        self.pending_call = None
        prefix = self.turn_dir / f"turn-{self.turn:02d}"
        seconds = len(voice_pcm) / 2 / SAMPLE_RATE
        peak = max((abs(sample) for sample in array.array("h", voice_pcm)), default=0)
        log.info("Turn %d: recorded %.1f s of voice; microphone peak %d/32767", self.turn, seconds, peak)
        if seconds < MIN_RECORDING_SECONDS:
            log.info("Turn %d: too short, ignored", self.turn)
            return None
        wav_bytes = to_wav(voice_pcm)
        prefix.with_name(prefix.name + "-voice.wav").write_bytes(wav_bytes)

        started = time.monotonic()
        question = transcribe(self.session, self.meta_key, wav_bytes)
        log.info("Turn %d: transcribe %.2f s -> %r", self.turn, time.monotonic() - started, question)
        if not question:
            log.info("Turn %d: empty transcript, nothing to answer", self.turn)
            return None

        self.begin_turn(started_at)
        if self.photos:
            for result in self.photos.refresh_sms():
                self.history.append({"role": "developer", "content": "Actual SMS delivery update: " + json.dumps(result)})

        started = time.monotonic()
        reply = answer(self.client, self.system_prompt, question, self.model, self.effort,
                       self.tools, self.run_tool, history=self.history,
                       max_rounds=6 if self.photos else MAX_TOOL_ROUNDS)
        log.info("Turn %d: %s %.2f s -> %r", self.turn, self.model, time.monotonic() - started, reply)
        if not reply:
            log.info("Turn %d: empty answer, nothing to say", self.turn)
            return None

        started = time.monotonic()
        speech = synthesize(self.session, self.cartesia_key, self.voice_id, reply)
        log.info("Turn %d: cartesia %.2f s -> %.1f s of audio", self.turn,
                 time.monotonic() - started, len(speech) / 2 / SAMPLE_RATE)
        prefix.with_name(prefix.name + "-answer.wav").write_bytes(to_wav(speech))
        return speech


def setup_logging(log_path):
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(log_path, encoding="utf-8")):
        handler.setFormatter(formatter)
        log.addHandler(handler)
    log.setLevel(logging.INFO)


def start_phone(public_url, http_port):
    """Loads contacts and Telnyx settings and starts the webhook/audio server for calls."""
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from phone_call import PhoneServer, TelnyxCalls
    from telnyx_call import phone_number
    values = {key: os.environ.get(key, "").strip()
              for key in ("TELNYX_API_KEY", "TELNYX_CONNECTION_ID", "TELNYX_FROM_NUMBER")}
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise SystemExit("Phone calls need in .env: " + ", ".join(missing))
    if not CONTACTS_PATH.exists():
        raise SystemExit("Phone calls need contacts.json (see contacts.example.json)")
    contacts = load_contacts()
    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY_PATH.read_text().strip()))
    calls = TelnyxCalls(values["TELNYX_API_KEY"], values["TELNYX_CONNECTION_ID"],
                        phone_number(values["TELNYX_FROM_NUMBER"]))
    phone = PhoneServer(calls, public_url, public_key, http_port)
    log.info("Phone calls enabled for %d contact(s): %s (events/audio on port %d)",
             len(contacts), ", ".join(contacts), http_port)
    return phone, contacts


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", help="ESP32 serial port")
    parser.add_argument("--model", default=CHAT_MODEL, help="Meta chat model")
    parser.add_argument("--effort", default=REASONING_EFFORT,
                        choices=["minimal", "low", "medium", "high", "xhigh", "max"], help="reasoning effort")
    parser.add_argument("--voice-id", default=DEFAULT_VOICE_ID, help="Cartesia voice ID")
    parser.add_argument("--public-url", help="enable phone calls: public HTTPS URL (e.g. ngrok) "
                                             "forwarding to --http-port")
    parser.add_argument("--http-port", type=int, default=8766, help="local port for Telnyx events and audio")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    meta_key = os.environ.get("META_API_KEY")
    cartesia_key = os.environ.get("CARTESIA_API_KEY")
    if not meta_key or not cartesia_key:
        raise SystemExit("META_API_KEY and CARTESIA_API_KEY must be set in .env")

    started = datetime.now().strftime("%Y%m%d-%H%M%S")
    turn_dir = LOG_DIR / f"voice-agent-{started}"
    turn_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(LOG_DIR / f"voice-agent-{started}.log")
    contacts, phone = {}, None
    if args.public_url:
        phone, contacts = start_phone(args.public_url, args.http_port)
    photos = None
    if os.environ.get("CAMERA_URL"):
        from photo_tools import PHOTO_ENV, PhotoTools
        missing = [key for key in PHOTO_ENV if not os.environ.get(key, "").strip()]
        if missing:
            raise SystemExit("Photos need in .env: " + ", ".join(missing))
        if not contacts:
            contacts = load_contacts()
        photos = PhotoTools({key: os.environ[key].strip() for key in PHOTO_ENV}, meta_key,
                            turn_dir / "photos", contacts)
        log.info("Photo restoration, Supabase and SMS tools enabled")
    agent = Agent(meta_key, cartesia_key, args.voice_id, SYSTEM_PROMPT_PATH.read_text().strip(), turn_dir,
                  args.model, args.effort, contacts, photos=photos, enable_calls=bool(phone))
    log.info("Models: %s, %s (effort %s), %s (voice %s)", TRANSCRIBE_MODEL, args.model, args.effort,
             TTS_MODEL, args.voice_id)
    log.info("Audio files for each turn: %s", turn_dir.relative_to(ROOT))

    link = VoiceLink(args.port or find_port(), log=log.info)
    try:
        if not link.wait_ready():
            log.info("No ready message from the ESP32 (it may not have reset); continuing")
        log.info("Hold the button, ask a question and release. Ctrl+C exits.")
        while True:
            voice = link.record()
            try:
                speech = agent.handle(voice, started_at=link.record_started_at)
            except Exception as error:  # keep the agent alive between turns
                log.error("Turn %d failed: %s", agent.turn, error)
                if agent.pending_call:
                    agent.remember_call_outcome(agent.pending_call[0], "not_started")
                    agent.pending_call = None
                agent.mark_idle()
                continue
            try:
                if speech:
                    started_play = time.monotonic()
                    link.play(speech)
                    log.info("Turn %d: played answer in %.1f s", agent.turn, time.monotonic() - started_play)
                if agent.pending_call and phone:
                    name, number = agent.pending_call
                    agent.pending_call = None
                    log.info("Turn %d: calling %s", agent.turn, name)
                    outcome = "failed"
                    try:
                        outcome = phone.call(link, number) or "failed"
                    finally:
                        agent.remember_call_outcome(name, outcome)
                    log.info("Turn %d: call outcome %s", agent.turn, outcome)
                    try:
                        announcement = CALL_OUTCOMES[outcome].format(name=name)
                        link.play(agent.say(announcement))
                        agent.history.append({"role": "assistant", "content": announcement})
                    except Exception as error:
                        log.error("Could not announce the call outcome: %s", error)
            finally:
                agent.mark_idle()
    except KeyboardInterrupt:
        log.info("Stopped")
    finally:
        link.close()


if __name__ == "__main__":
    main()
