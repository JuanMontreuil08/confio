# Confio

A simple gadget and personal AI agent for older adults who don't use smartphones.

Confio is being built for the **Meta Global AI Developer Hackathon** (submission due November 4, 2026). Its first user is an aunt who lives almost alone. The goal is to give her a helper she can use by pressing a button and talking, with no screen and no apps.

> **Status:** early, work in progress. This README describes the project at a high level. Details will change as we build step by step.

## The idea

Many older adults find smartphones confusing, but they can still benefit from an AI assistant. Confio puts that assistant behind a handheld device with a few large buttons. The user presses a button, asks a question out loud in Spanish, and gets a spoken answer.

## First target flow: "Where is my bottle?"

1. **Press a button.** The user presses **Ver** ("Look") on the handheld gadget, and it notifies the laptop over WiFi.
2. **Ask out loud.** The laptop records the question (for example, *"¿Dónde está mi botella?"*) and turns it into text.
3. **Agent reasoning.** The agent works out what the user wants, picks the target object ("bottle"), and decides to use the camera.
4. **Take a photo.** The agent asks a camera fixed in the room for a photo.
5. **Look for the object.** The agent runs a vision model on the photo to find the object.
6. **Search the room.** If the object isn't found, the camera rotates on a servo and tries again until it finds it or has scanned the whole room.
7. **Answer by voice.** The agent replies in Spanish (for example, *"Está en la mesa, a la izquierda"*) or says it couldn't find the object.

## Components

| Part | What it does |
|------|--------------|
| **Gadget** | ESP32 DevKit with three buttons: *Llamar* (Call), *Ver* (Look), *Investigar* (Research). |
| **Camera** | ESP32-CAM (OV2640) mounted on a servo, fixed in the room. |
| **Laptop** | Runs the Python agent, with a microphone and speaker for now. |
| **Models** | Meta API in the cloud: Muse Spark for reasoning, SAM 3.1 for finding objects. |

## Agent tools (initial)

- `take_photo()` gets an image from the ESP32-CAM.
- `rotate_camera(angle)` moves the servo (for example 0 / 90 / 180).
- `find_object(image, name)` uses SAM 3.1 to report whether the object was found and where.
- `speak(text)` says the answer out loud.

## Roadmap

1. Button to laptop, laptop audio, one photo plus SAM, spoken answer.
2. Add the servo search loop.
3. Move the microphone and speaker onto the gadget (INMP441 + MAX98357).
4. Later: the *Llamar* and *Investigar* buttons.

## Principles

- Keep it simple and demo-first.
- The user-facing language is Spanish; code and docs are in English.

## Call button preview

The protoboard Call button (GPIO 26) and Look button (GPIO 27) have been verified
by the user. The Call UI prototype lives in `web/call/` on branch
`codex/llamar-prototype`.

Close PlatformIO Serial Monitor and run from the repository root:

```sh
~/.platformio/penv/bin/python scripts/call_preview.py
```

Open **http://localhost:8765/** to preview it immediately. Pressing Call also
prints the URL and opens the page. For a real browser-to-phone audio test:

```sh
python3 -m pip install -r scripts/call-requirements.txt -r scripts/telnyx-requirements.txt
python3 scripts/call_preview.py --live --to "+51XXXXXXXXX"
```

Click **Talk with your agent** and allow the microphone. The phone's voice drives
the waveform; Mute silences your microphone and End call hangs up. No AI agent
is connected yet. See [the browser call instructions](web/call/README.md).

## Telnyx call test setup

The Voice API application and temporary signed webhook receiver are configured.
See [the local Telnyx setup](scripts/TELNYX.md) for connection values and commands.
The user confirmed the full button-to-browser call flow, two-way audio, remote
voice waves, microphone mute and hangup. Replace the example destination in the
commands with your own number; the personal test number is not committed.
