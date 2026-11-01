#!/usr/bin/env python3
"""Receive framed ESP32 audio and save each button press as a WAV file."""
import argparse
import array
from datetime import datetime
import math
from pathlib import Path
import struct
import sys
import wave

import serial
from serial.tools import list_ports

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "media" / "output"
HEADER = struct.Struct("<4sBI")
MAGIC = b"MIC1"


def find_port():
    ports = [p.device for p in list_ports.comports() if p.vid is not None]
    if len(ports) != 1:
        raise SystemExit("Specify the ESP32 port with --port (detected: " + ", ".join(ports) + ")")
    return ports[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="ESP32 serial port")
    args = parser.parse_args()
    port = args.port or find_port()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    audio = None
    path = None
    buffer = bytearray()
    sums = [0, 0]
    counts = [0, 0]
    sample_index = 0
    try:
        with serial.Serial(port, 921600, timeout=1) as device:
            print(f"Listening on {port}. Hold the button to record; Ctrl+C exits.", flush=True)
            while True:
                buffer.extend(device.read(device.in_waiting or 1))
                while True:
                    start = buffer.find(MAGIC)
                    if start < 0:
                        buffer[:] = buffer[-3:]
                        break
                    del buffer[:start]
                    if len(buffer) < HEADER.size:
                        break
                    _, kind, length = HEADER.unpack_from(buffer)
                    if kind not in b'SDFE' or length > 1024:
                        del buffer[0]
                        continue
                    if len(buffer) < HEADER.size + length:
                        break
                    payload = bytes(buffer[HEADER.size:HEADER.size + length])
                    del buffer[:HEADER.size + length]
                    if kind == ord('S'):
                        if audio:
                            audio.close()
                        path = OUTPUT / ("voice-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".wav")
                        audio = wave.open(str(path), "wb")
                        audio.setnchannels(2)
                        audio.setsampwidth(2)
                        audio.setframerate(16000)
                        sums = [0, 0]
                        counts = [0, 0]
                        sample_index = 0
                        print("Grabando voz...", flush=True)
                    elif kind == ord('D') and audio:
                        audio.writeframes(payload)
                        samples = array.array('h')
                        samples.frombytes(payload)
                        if sys.byteorder != 'little':
                            samples.byteswap()
                        for sample in samples:
                            channel = sample_index % 2
                            sums[channel] += sample * sample
                            counts[channel] += 1
                            sample_index += 1
                    elif kind == ord('F') and audio:
                        audio.close()
                        audio = None
                        print(f"Grabación guardada: {path}", flush=True)
                        levels = [round(math.sqrt(sums[i] / counts[i]), 1) if counts[i] else 0 for i in range(2)]
                        print(f"Nivel de canales 1 y 2: {levels[0]}, {levels[1]}", flush=True)
                    elif kind == ord('E'):
                        print(payload.decode("utf-8", errors="replace"), flush=True)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        if audio:
            audio.close()
            print(f"Partial recording saved: {path}")


if __name__ == "__main__":
    main()
