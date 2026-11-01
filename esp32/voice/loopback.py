#!/usr/bin/env python3
"""Hardware test for esp32/voice: hold the button, speak, release, hear it back.

With --wav, plays a 16 kHz 16-bit WAV through the ESP32 speaker once instead.
"""
import argparse
import array
import math
from pathlib import Path
import sys
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent))
from voice_link import SAMPLE_RATE, VoiceLink, find_port, mono_channel  # noqa: E402


def read_wav(path):
    with wave.open(str(path), "rb") as audio:
        if audio.getframerate() != SAMPLE_RATE or audio.getsampwidth() != 2 or audio.getnchannels() > 2:
            raise SystemExit("The WAV must be 16 kHz, 16-bit, mono or stereo")
        pcm = audio.readframes(audio.getnframes())
        return mono_channel(pcm) if audio.getnchannels() == 2 else pcm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="ESP32 serial port")
    parser.add_argument("--wav", type=Path, help="play this WAV once instead of the echo test")
    args = parser.parse_args()
    link = VoiceLink(args.port or find_port())
    try:
        if not link.wait_ready():
            print("No ready message (the board may not have reset); continuing.")
        if args.wav:
            print(f"Playing {args.wav}...")
            link.play(read_wav(args.wav))
            print("Done.")
            return
        print("Hold the button, speak and release; your voice plays back. Ctrl+C exits.")
        while True:
            voice = link.record()
            samples = array.array("h", voice)
            level = math.sqrt(sum(x * x for x in samples) / len(samples)) if samples else 0
            print(f"Recorded {len(samples) / SAMPLE_RATE:.1f} s, level {level:.0f}. Playing back...")
            link.play(voice)
            print("Ready.")
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        link.close()


if __name__ == "__main__":
    main()
