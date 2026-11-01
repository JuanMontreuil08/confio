"""Verify that firmware audio assets preserve the validated WAV channel."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import wave


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "speaker_audio", ROOT / "esp32" / "speaker" / "prepare_audio.py")
audio_builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audio_builder)


class SpeakerAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.project = root / "esp32" / "speaker"
        self.source = root / "media" / "output" / audio_builder.SAMPLE_NAME
        self.source.parent.mkdir(parents=True)

    def write_wav(self, data, channels=2, width=2, rate=16000):
        with wave.open(str(self.source), "wb") as audio:
            audio.setnchannels(channels)
            audio.setsampwidth(width)
            audio.setframerate(rate)
            audio.writeframes(data)

    def test_extracts_only_voice_channel_without_changing_signed_samples(self):
        self.write_wav(struct.pack("<8h", -32768, 99, -1, 99, 0, 99, 32767, 99))
        audio_builder.prepare_audio(self.project)
        self.assertEqual((self.project / "data/voice.pcm").read_bytes(),
                         struct.pack("<4h", -32768, -1, 0, 32767))
        header = (self.project / "include/audio_info.h").read_text()
        self.assertIn("kAudioSamples = 4;", header)
        self.assertIn("kAudioSampleRate = 16000;", header)

    def test_accepts_mono_pcm_without_modification(self):
        pcm = struct.pack("<3h", -2000, 0, 2000)
        self.write_wav(pcm, channels=1)
        audio_builder.prepare_audio(self.project)
        self.assertEqual((self.project / "data/voice.pcm").read_bytes(), pcm)

    def test_rejects_unsupported_format_and_empty_recordings(self):
        cases = [
            (b"\x80", 1, 1, 16000),
            (b"\x00" * 6, 3, 2, 16000),
            (b"\x00\x00", 1, 2, 8000),
            (b"", 1, 2, 16000),
        ]
        for data, channels, width, rate in cases:
            with self.subTest(channels=channels, width=width, rate=rate, size=len(data)):
                self.write_wav(data, channels, width, rate)
                with self.assertRaises(ValueError):
                    audio_builder.prepare_audio(self.project)
                self.assertFalse((self.project / "data/voice.pcm").exists())

    def test_rejects_truncated_payload(self):
        self.write_wav(struct.pack("<4h", -100, 0, 100, 0))
        self.source.write_bytes(self.source.read_bytes()[:-2])
        with self.assertRaisesRegex(ValueError, "Truncated"):
            audio_builder.prepare_audio(self.project)

    def test_rejects_recording_over_flash_budget(self):
        self.write_wav(b"\x00\x00" * (16 * 16000 + 1), channels=1)
        with self.assertRaisesRegex(ValueError, "16 seconds"):
            audio_builder.prepare_audio(self.project)


if __name__ == "__main__":
    unittest.main()
