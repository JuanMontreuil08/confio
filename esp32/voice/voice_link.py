"""Laptop side of the esp32/voice serial protocol.

ESP32 -> laptop frames ("MIC1"): S start, D voice PCM, F finish, A played, I info, E error.
Laptop -> ESP32 frames ("SPK1"): P mono PCM to play, E end of playback.
Every frame is: 4-byte magic, 1-byte type, little-endian uint32 length, payload.
"""
import struct
import time

import serial
from serial.tools import list_ports

BAUD_RATE = 921600
SAMPLE_RATE = 16000
HEADER = struct.Struct("<4sBI")
MIC_MAGIC = b"MIC1"
SPEAKER_MAGIC = b"SPK1"
MIC_TYPES = b"SDFAIE"
MAX_MIC_PAYLOAD = 1024
PLAY_CHUNK_BYTES = 1024  # 512 mono samples, the firmware maximum
WINDOW = 8  # frames in flight; 8 KiB stays well under the 16 KiB serial buffer
READY_MESSAGE = b"Voice loop ready"  # sent once at boot, after any info lines
ACK_TIMEOUT = 3.0  # seconds without any frame before playback gives up


def find_port():
    ports = [p.device for p in list_ports.comports() if p.vid is not None]
    if len(ports) != 1:
        raise SystemExit("Specify the ESP32 port with --port (detected: " + ", ".join(ports) + ")")
    return ports[0]


def pack(kind, payload=b""):
    return HEADER.pack(SPEAKER_MAGIC, ord(kind), len(payload)) + payload


class FrameReader:
    """Splits the ESP32 byte stream into (type, payload) frames, skipping boot noise."""

    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while True:
            start = self.buffer.find(MIC_MAGIC)
            if start < 0:
                del self.buffer[:-3]
                return frames
            del self.buffer[:start]
            if len(self.buffer) < HEADER.size:
                return frames
            _, kind, length = HEADER.unpack_from(self.buffer)
            if kind not in MIC_TYPES or length > MAX_MIC_PAYLOAD:
                del self.buffer[0]
                continue
            if len(self.buffer) < HEADER.size + length:
                return frames
            frames.append((chr(kind), bytes(self.buffer[HEADER.size:HEADER.size + length])))
            del self.buffer[:HEADER.size + length]


def mono_channel(stereo_pcm):
    """Keeps channel 1 of the mic's interleaved stereo PCM (channel 2 is silent)."""
    usable = len(stereo_pcm) - len(stereo_pcm) % 4
    return b"".join(stereo_pcm[i:i + 2] for i in range(0, usable, 4))


class VoiceLink:
    def __init__(self, port, log=print):
        self.device = serial.Serial(port, BAUD_RATE, timeout=0.1)
        self.reader = FrameReader()
        self.pending = []
        self.log = log

    def wait_ready(self, seconds=4.0):
        """Opening the port usually resets the ESP32; wait for its ready message."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.pending.extend(self.reader.feed(self.device.read(self.device.in_waiting or 1)))
            while self.pending:
                kind, payload = self.pending.pop(0)
                if kind in "IE":
                    self.log(("ESP32: " if kind == "I" else "ESP32 error: ") + payload.decode("utf-8", "replace"))
                if kind == "I" and payload == READY_MESSAGE:
                    return True
        return False

    def close(self):
        self.device.close()

    def next_frame(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.pending:
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError("The ESP32 stopped responding")
            self.pending.extend(self.reader.feed(self.device.read(self.device.in_waiting or 1)))
        kind, payload = self.pending.pop(0)
        if kind in "IE":
            self.log(("ESP32: " if kind == "I" else "ESP32 error: ") + payload.decode("utf-8", "replace"))
        return kind, payload

    def record(self):
        """Waits for one button press and returns the voice as mono 16 kHz PCM."""
        while self.next_frame()[0] != "S":
            pass
        self.log("Recording...")
        stereo = bytearray()
        while True:
            kind, payload = self.next_frame()
            if kind == "D":
                stereo.extend(payload)
            elif kind == "F":
                return mono_channel(stereo)

    def play(self, mono_pcm):
        """Sends mono 16 kHz PCM to the speaker, paced by the ESP32's acknowledgements."""
        mono_pcm = mono_pcm[:len(mono_pcm) - len(mono_pcm) % 2]
        frames = [pack("P", mono_pcm[i:i + PLAY_CHUNK_BYTES])
                  for i in range(0, len(mono_pcm), PLAY_CHUNK_BYTES)]
        frames.append(pack("E"))
        sent = acknowledged = 0
        while acknowledged < len(frames):
            while sent < len(frames) and sent - acknowledged < WINDOW:
                self.device.write(frames[sent])
                sent += 1
            kind, payload = self.next_frame(timeout=ACK_TIMEOUT)
            if kind == "A":
                acknowledged += 1
            elif kind == "I" and payload == READY_MESSAGE:
                raise RuntimeError("The ESP32 restarted during playback")
