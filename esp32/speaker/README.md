# MAX98357A speaker playback test

Standalone playback of the retained microphone WAV on an ESP32 DevKit V1,
MAX98357A and the user's 8-ohm, 3 W speaker. The sample is embedded in flash:
after uploading, USB supplies power only. No laptop audio streaming, WiFi,
Telnyx, Cartesia or agent is involved. The user confirmed playback at the final
80% PCM amplitude setting. A loose VIN jumper caused intermittent silence
during testing; the physical power connector still needs a firm connection.

## Wiring

Disconnect USB before changing connections. Preserve the microphone wiring
(SCK 18, WS 5, SD 22, VDD 3V3). These amplifier signals use separate GPIOs:

| Amplifier | ESP32 / breadboard |
| --- | --- |
| VIN | Direct female-to-female jumper to USB-fed ESP32 VIN |
| GND | Common GND rail connected to ESP32 GND |
| SD | Leave unconnected (use this breakout's onboard enable resistor) |
| BCLK | RX2 / GPIO 16 |
| LRC | TX2 / GPIO 17 |
| DIN | D19 / GPIO 19 |
| GAIN | Leave unconnected |

On this DevKit the USB-fed VIN measured approximately 4.6 V. The earlier
breadboard supply route did not deliver voltage to the amplifier; direct VIN
restored audible playback. The user also observed that pressing the loose VIN
connector restored sound. Secure the connection before relying on playback;
successful I2S logs do not prove the amp is powered or the speaker is audible.
Keep VIN separate from microphone 3V3 and GND.
If the VIN pin is physically inaccessible, do not improvise a connection.

Speaker purple lead (marked + on the speaker) goes to amp +; gray goes to amp -.
Secure exposed conductors in the screw terminals. Neither speaker terminal
connects to ESP32 GND; the amp has a bridge output.

## Build and upload

Run from the repository root. Stop the mic receiver and all serial monitors
before uploading. Uploading this test temporarily replaces microphone/button
firmware on the board, but does not change its source or wiring.

```sh
~/.platformio/penv/bin/pio run -d esp32/speaker -e esp32dev
~/.platformio/penv/bin/pio run -d esp32/speaker -e esp32dev -t upload --upload-port /dev/cu.usbserial-0001
```

Adjust the port if necessary. Wait three seconds: the voice sample plays at
80% of original PCM amplitude, then repeats after five seconds of silence.
No button press is needed. Unplug USB to stop. This amplitude setting is not
a calibrated percentage of perceived volume or speaker power.

Change `kVolumePercent` in `src/main.cpp` and upload again to adjust the digital
amplitude. The default remains the final hardware-tested setting of 80%.

Optional logs (115200 baud):

```sh
~/.platformio/penv/bin/pio device monitor --port /dev/cu.usbserial-0001 --baud 115200
```

`prepare_audio.py` runs before each build, validates the retained
`media/output/voice-20261031-194442-058191.wav` and extracts channel 1 without
normalization or resampling. Generated PCM/header files are ignored. Playback
duplicates the voice in both I2S slots and attenuates it in firmware.

To restore microphone capture, stop any monitor and upload its original project:

```sh
~/.platformio/penv/bin/pio run -d esp32/mic -e esp32dev -t upload --upload-port /dev/cu.usbserial-0001
```

## Verification

The ESP32 build and physical WAV playback passed. The audio preparation checks
run with the existing Python test suite:

```sh
python3 -m unittest discover -s scripts/tests -v
```
