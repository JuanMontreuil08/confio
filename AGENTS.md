# AGENTS.md

## Project
A simple gadget and personal AI agent for older adults who don't use smartphones (first user: an aunt who lives almost alone). Built for the Meta Global AI Developer Hackathon (due Nov 4, 2026).

This file describes the **first target flow only**, at a high level. Details will change; we build progressively.

## First target flow: "Where is my bottle?"
1. **Button press.** The user presses the "Look" button on the handheld gadget (ESP32 DevKit + buttons). The gadget notifies the laptop over WiFi.
2. **Voice instruction.** The laptop records the user's question (e.g. "¿Dónde está mi botella?") and turns it into text.
3. **Agent reasoning.** The agent (Python, on the laptop) understands the request, extracts the target object ("bottle"), and decides to call the camera tool.
4. **Camera tool.** The agent asks the ESP32-CAM (fixed in the room) for a photo over WiFi; the camera returns it.
5. **Vision.** The agent runs SAM 3.1 on the photo to look for the object.
6. **Search loop.** If the object is not found, the agent rotates the camera with the servo to a new angle, takes another photo, and checks again, until it finds the object or has scanned the whole room.
7. **Spoken answer.** The agent replies by voice in Spanish (e.g. "Está en la mesa, a la izquierda"), or says it couldn't find it.

## Components
- **Gadget:** ESP32 DevKit with buttons (Llamar / Ver / Investigar). Only "Ver" is used in this flow.
- **Camera:** ESP32-CAM (OV2640) on a servo, fixed in the room, own 5V 2A supply.
- **Laptop:** runs the agent, microphone and speaker for now.
- **Models (Meta API, cloud):** Muse Spark for reasoning, SAM 3.1 for finding objects. Speech-to-text / text-to-speech: Meta if available, otherwise a separate service.

## Agent tools (initial)
- `take_photo()` returns an image from the ESP32-CAM.
- `rotate_camera(angle)` moves the servo (e.g. 0 / 90 / 180).
- `find_object(image, name)` uses SAM 3.1 and reports found or not, plus location.
- `speak(text)` gives the answer by voice.

## Build order
1. Button to laptop, laptop audio, one photo plus SAM, voice answer.
2. Add the servo search loop.
3. Later: move mic and speaker onto the gadget (INMP441 + MAX98357).

## Guidelines
- Keep it simple and demo-first.
- User-facing language is Spanish; code and docs are in English.
