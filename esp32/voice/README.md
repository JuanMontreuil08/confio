# Push-to-talk voice loop over USB serial

Combines the validated microphone (`esp32/mic`) and speaker (`esp32/speaker`)
on one ESP32 DevKit V1 so the laptop can hear the user and answer through the
speaker without re-flashing. Wiring is unchanged; see those READMEs. The
`mic` and `speaker` projects stay untouched: upload either one to go back.

| Part | Pins |
| --- | --- |
| INMP441 (I2S0) | SCK 18, WS 5, SD 22 |
| MAX98357A (I2S1) | BCLK 16 (RX2), LRC 17 (TX2), DIN 19 |
| Button | GPIO 23 to GND (internal pull-up) |

Playback runs at 100% PCM amplitude (`kVolumePercent`). If audio distorts or
the board resets on loud phrases, check the VIN jumper or lower that value.

## Protocol (921600 baud)

Every frame is a 4-byte magic, a 1-byte type, a little-endian uint32 length and
the payload.

- ESP32 to laptop, `MIC1`: `S` button pressed, `D` voice PCM (stereo, channel 1
  carries the mic), `F` button released, `A` one playback frame played,
  `I` info, `E` error.
- Laptop to ESP32, `SPK1`: `P` up to 1024 bytes of mono 16 kHz 16-bit PCM,
  `E` end of playback.

The laptop keeps at most 8 `P` frames in flight and waits for an `A` for each,
so the 16 KiB serial receive buffer never overflows. The button is ignored
while audio plays. The transport is separate from the audio code so the same
frames can later travel over WiFi.

## Upload and test

Run from the repository root. Stop every serial monitor or receiver first.

```sh
~/.platformio/penv/bin/pio run -d esp32/voice -e esp32dev -t upload --upload-port /dev/cu.usbserial-0001
python3 -m pip install -r esp32/voice/requirements.txt
python3 esp32/voice/loopback.py --port /dev/cu.usbserial-0001
```

Hold the button, speak and release: your voice plays back through the speaker.
To play a WAV file (16 kHz, 16-bit) once instead:

```sh
python3 esp32/voice/loopback.py --port /dev/cu.usbserial-0001 --wav media/output/voice-20261031-194442-058191.wav
```

`voice_link.py` is the reusable laptop side; the agent will use `record()` and
`play()`. Protocol tests run with `python3 -m unittest discover -s scripts/tests -v`.

## Boot mic check

At boot the firmware reads about 0.5 s of room noise and reports
`Mic check peak N`. A working mic never reads exactly 0; `0` means no signal,
which during testing came from a loose microphone jumper, not the firmware.
