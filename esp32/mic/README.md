# Push-to-talk microphone capture

Validated on the ESP32 DevKit V1 with an INMP441. Hold the button to record,
then release it to save a local WAV. The user confirmed clear voice capture.

## Wiring

Disconnect USB power before changing connections. Use the direct female-to-female
signal connections from the validated setup, without routing through the breadboard.

| INMP441 | ESP32 |
| --- | --- |
| VDD | 3V3 |
| GND | GND |
| L/R | GND |
| SCK | GPIO 18 |
| WS | GPIO 5 |
| SD | GPIO 22 |

Connect a normally open button between GPIO 23 and GND (internal pull-up).
Keep this pin mapping: earlier trials on WS 19 and SD 21 did not produce reliable
voice. Testing did not establish whether those GPIOs, contacts or wiring caused it.

## Upload and record

Run from the repository root. Stop the receiver and close all serial monitors
before uploading; only one process can use the ESP32 serial port at a time.

```sh
python3 -m pip install -r esp32/mic/requirements.txt
~/.platformio/penv/bin/pio run -d esp32/mic -e esp32dev -t upload --upload-port /dev/cu.usbserial-0001
python3 esp32/mic/record.py --port /dev/cu.usbserial-0001
```

Replace the port with the actual device. Omit `--port` on the receiver to select
it automatically when exactly one USB serial device is connected.

Wait for the listening message, hold the button, speak, and release. Each press
saves a stereo 16 kHz, 16-bit PCM WAV under `media/output/`. The validated setup
carries the microphone signal in channel 1; channel 2 is effectively silent.
The receiver prints both channel RMS levels. Press Ctrl+C to stop.

The firmware sends framed binary audio at 921600 baud, so use `record.py` rather
than a serial monitor. This capture is local; no transcription or upload occurs.

## Validated sample

The final hardware test is retained at
[`voice-20261031-194442-058191.wav`](../../media/output/voice-20261031-194442-058191.wav).
Earlier failed captures and the temporary diagnostic firmware were removed.
