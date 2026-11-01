"""Verify the laptop side of the esp32/voice serial protocol with a fake port."""
import importlib.util
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("voice_link", ROOT / "esp32" / "voice" / "voice_link.py")
voice_link = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(voice_link)


def mic_frame(kind, payload=b""):
    return struct.pack("<4sBI", b"MIC1", ord(kind), len(payload)) + payload


class FakeSerial:
    """Acknowledges each SPK1 frame like the firmware, tracking frames in flight."""

    def __init__(self, incoming=b""):
        self.incoming = bytearray(incoming)
        self.written = bytearray()
        self.in_flight = 0
        self.max_in_flight = 0

    @property
    def in_waiting(self):
        return len(self.incoming)

    def read(self, size):
        if not self.incoming and self.in_flight:
            self.in_flight -= 1
            self.incoming.extend(mic_frame("A"))
        data = bytes(self.incoming[:size])
        del self.incoming[:size]
        return data

    def write(self, data):
        self.written.extend(data)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)


def make_link(fake):
    link = voice_link.VoiceLink.__new__(voice_link.VoiceLink)
    link.device = fake
    link.reader = voice_link.FrameReader()
    link.pending = []
    link.log = lambda message: None
    return link


class VoiceLinkTests(unittest.TestCase):
    def test_reader_skips_boot_noise_and_split_frames(self):
        reader = voice_link.FrameReader()
        stream = b"ets Jun  8 2016 boot\r\n" + mic_frame("I", b"ready") + mic_frame("S")
        frames = reader.feed(stream[:30]) + reader.feed(stream[30:])
        self.assertEqual(frames, [("I", b"ready"), ("S", b"")])

    def test_record_keeps_channel_one(self):
        stereo = struct.pack("<4h", 100, -1, 200, -2)
        fake = FakeSerial(mic_frame("S") + mic_frame("D", stereo) + mic_frame("F"))
        self.assertEqual(make_link(fake).record(), struct.pack("<2h", 100, 200))

    def test_play_chunks_audio_and_limits_frames_in_flight(self):
        fake = FakeSerial()
        pcm = bytes(range(256)) * 20 + b"\x01"  # odd trailing byte is dropped
        make_link(fake).play(pcm)
        payloads, offset = [], 0
        while offset < len(fake.written):
            magic, kind, length = struct.unpack_from("<4sBI", fake.written, offset)
            self.assertEqual(magic, b"SPK1")
            payloads.append((chr(kind), fake.written[offset + 9:offset + 9 + length]))
            offset += 9 + length
        self.assertEqual(payloads[-1], ("E", b""))
        self.assertTrue(all(len(p) <= 1024 for _, p in payloads))
        self.assertEqual(b"".join(p for _, p in payloads), pcm[:-1])
        self.assertLessEqual(fake.max_in_flight, voice_link.WINDOW)
        self.assertEqual(fake.in_flight, 0)

    def test_play_times_out_when_the_board_goes_silent(self):
        fake = FakeSerial()
        fake.read = lambda size: b""
        link = make_link(fake)
        voice_link.ACK_TIMEOUT = 0.05
        self.addCleanup(setattr, voice_link, "ACK_TIMEOUT", 3.0)
        with self.assertRaises(TimeoutError):
            link.play(b"\0\0" * 10)

    def test_play_ignores_other_info_messages(self):
        info = voice_link.HEADER.pack(b"MIC1", ord("I"), 4) + b"note"
        make_link(FakeSerial(info)).play(b"\0\0" * 10)

    def test_play_reports_a_restart(self):
        fake = FakeSerial(voice_link.HEADER.pack(b"MIC1", ord("I"), 16) + b"Voice loop ready")
        with self.assertRaises(RuntimeError):
            make_link(fake).play(b"\0\0" * 10)


if __name__ == "__main__":
    unittest.main()
