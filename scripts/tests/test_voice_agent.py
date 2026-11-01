"""Verify the voice agent's API requests and turn handling with fake services."""
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
import wave
import io

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("voice_agent", ROOT / "scripts" / "voice_agent.py")
voice_agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(voice_agent)


class FakeResponse:
    def __init__(self, status=200, body=None, content=b""):
        self.status_code = status
        self.body = body or {}
        self.content = content
        self.text = json.dumps(self.body)

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def text_response(text, response_id="resp_text"):
    message = types.SimpleNamespace(type="message")
    return types.SimpleNamespace(id=response_id, output=[message], output_text=text)


class FakeItem(types.SimpleNamespace):
    def model_dump(self, exclude_none=False):
        return dict(vars(self))


def tool_response(name="get_current_datetime", arguments="{}", response_id="resp_tool"):
    call = FakeItem(type="function_call", name=name, arguments=arguments, call_id="call_1")
    return types.SimpleNamespace(id=response_id, output=[call], output_text="")


def fake_client(*responses):
    """Fake Responses API: returns the given responses in order and records each request."""
    if len(responses) == 1 and isinstance(responses[0], str):
        responses = (text_response(responses[0]),)
    queue = list(responses)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return queue.pop(0)
    return types.SimpleNamespace(responses=types.SimpleNamespace(create=create)), calls


class VoiceAgentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def make_agent(self, session, reply):
        agent = voice_agent.Agent.__new__(voice_agent.Agent)
        agent.session = session
        agent.client, self.chat_calls = fake_client(reply)
        agent.meta_key, agent.cartesia_key = "meta-test", "cartesia-test"
        agent.voice_id, agent.system_prompt = "voice-test", "system-test"
        agent.turn_dir, agent.turn = self.dir, 0
        agent.model = voice_agent.CHAT_MODEL
        agent.effort = voice_agent.REASONING_EFFORT
        agent.contacts, agent.tools, agent.pending_call = {}, voice_agent.TOOLS, None
        return agent

    def test_full_turn_calls_each_service_and_saves_audio(self):
        speech = b"\x01\x00" * 1600
        session = FakeSession(FakeResponse(body={"transcript": " ¿Qué hora es? "}),
                              FakeResponse(content=speech))
        agent = self.make_agent(session, "Son las tres.")
        self.assertEqual(agent.handle(b"\x00\x00" * 16000), speech)

        url, kwargs = session.calls[0]
        self.assertEqual(url, voice_agent.TRANSCRIBE_URL)
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer meta-test"})
        request = json.loads(kwargs["files"]["request"][1])
        self.assertEqual(request["model"], "muse-voice-transcribe-1.0")
        self.assertEqual(request["audioEncoding"], "WAV")
        with wave.open(io.BytesIO(kwargs["files"]["audio"][1])) as audio:
            self.assertEqual((audio.getnchannels(), audio.getframerate()), (1, 16000))

        chat = self.chat_calls[0]
        self.assertEqual(chat["model"], "muse-spark-1.3")
        self.assertEqual(chat["input"], [{"role": "system", "content": "system-test"},
                                         {"role": "user", "content": "¿Qué hora es?"}])
        self.assertEqual(chat["tools"][0]["name"], "get_current_datetime")
        self.assertEqual(chat["tools"][1], {"type": "web_search", "search_context_size": "medium"})
        self.assertEqual(chat["extra_body"]["max_tool_calls"], 2)
        self.assertEqual(chat["reasoning"], {"effort": "medium"})
        self.assertIs(chat["store"], False)
        self.assertEqual(chat["include"], ["reasoning.encrypted_content", "web_search_call.results"])

        url, kwargs = session.calls[1]
        self.assertEqual(url, voice_agent.CARTESIA_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer cartesia-test")
        self.assertEqual(kwargs["json"]["model_id"], "sonic-3.6")
        self.assertEqual(kwargs["json"]["transcript"], "Son las tres.")
        self.assertEqual(kwargs["json"]["output_format"],
                         {"container": "raw", "encoding": "pcm_s16le", "sample_rate": 16000})
        self.assertTrue((self.dir / "turn-01-voice.wav").exists())
        self.assertTrue((self.dir / "turn-01-answer.wav").exists())

    def test_datetime_tool_returns_local_time_and_weekday(self):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 10, 31, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
        self.assertEqual(voice_agent.get_current_datetime(now),
                         {"datetime": "2026-10-31T23:30-05:00", "weekday": "Saturday"})

    def test_tool_call_result_is_sent_back_to_the_model(self):
        client, calls = fake_client(tool_response(), text_response("Hoy es sábado."))
        self.assertEqual(voice_agent.answer(client, "system", "¿Qué día es?"), "Hoy es sábado.")
        second = calls[1]
        self.assertNotIn("previous_response_id", second)
        history = second["input"]
        self.assertEqual([item.get("role") or item["type"] for item in history],
                         ["system", "user", "function_call", "function_call_output"])
        self.assertEqual(history[3]["call_id"], "call_1")
        self.assertIn("weekday", json.loads(history[3]["output"]))

    def test_web_search_answer_logs_sources_and_skips_commentary(self):
        def message(text, phase=None):
            block = types.SimpleNamespace(type="output_text", text=text, annotations=[])
            return types.SimpleNamespace(type="message", phase=phase, content=[block])
        result = {"title": "RadiologyInfo", "url": "https://radiologyinfo.org/x"}  # SDK leaves these as dicts
        search = types.SimpleNamespace(type="web_search_call", results=[result],
                                       action={"query": "histerosonografía"})
        output = [message("Voy a buscarlo.", "commentary"), search,
                  message("Según **RadiologyInfo**, es una ecografía [RadiologyInfo](https://radiologyinfo.org/x).")]
        client, calls = fake_client(types.SimpleNamespace(id="r", output=output, output_text="ignored"))
        with self.assertLogs("voice_agent", "INFO") as logs:
            reply = voice_agent.answer(client, "system", "¿Qué es una histerosonografía?")
        self.assertEqual(reply, "Según RadiologyInfo, es una ecografía RadiologyInfo.")
        self.assertEqual(len(calls), 1)  # the server runs the search; no second request
        self.assertIn("web_search_call.results", calls[0]["include"])
        joined = "\n".join(logs.output)
        self.assertIn("Web search: 'histerosonografía'", joined)
        self.assertIn("result: RadiologyInfo <https://radiologyinfo.org/x>", joined)

    def test_spoken_removes_urls_and_citation_marks(self):
        self.assertEqual(voice_agent.spoken("Vea https://a.pe/b. Listo【3†fuente】 [2]"), "Vea. Listo")

    def test_bad_tool_requests_return_errors_to_the_model(self):
        self.assertIn("error", voice_agent.run_tool("delete_everything", "{}"))
        self.assertIn("error", voice_agent.run_tool("get_current_datetime", "not json"))
        self.assertIn("error", voice_agent.run_tool("get_current_datetime", '{"city": "Lima"}'))

    def test_tool_loop_is_capped(self):
        responses = [tool_response(response_id=f"r{i}") for i in range(voice_agent.MAX_TOOL_ROUNDS + 1)]
        client, calls = fake_client(*responses)
        with self.assertRaisesRegex(RuntimeError, "still requesting tools"):
            voice_agent.answer(client, "system", "loop")
        self.assertEqual(len(calls), voice_agent.MAX_TOOL_ROUNDS + 1)

    def test_short_press_is_ignored_without_api_calls(self):
        session = FakeSession()
        self.assertIsNone(self.make_agent(session, "x").handle(b"\x00\x00" * 100))
        self.assertEqual(session.calls, [])

    def test_empty_transcript_skips_model_and_speech(self):
        session = FakeSession(FakeResponse(body={"transcript": ""}))
        agent = self.make_agent(session, "x")
        self.assertIsNone(agent.handle(b"\x00\x00" * 16000))
        self.assertEqual(self.chat_calls, [])
        self.assertEqual(len(session.calls), 1)

    def test_http_errors_are_reported(self):
        session = FakeSession(FakeResponse(status=401, body={"error": "bad key"}))
        with self.assertRaisesRegex(RuntimeError, "Transcribe HTTP 401"):
            self.make_agent(session, "x").handle(b"\x00\x00" * 16000)


if __name__ == "__main__":
    unittest.main()
