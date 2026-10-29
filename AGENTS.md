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

## Hardware test progress and next steps
- The ESP32 DevKit V1 button is wired between GPIO 27 and GND using the internal pull-up. The user verified repeated presses in the serial monitor. The tested four-leg button needs connections on different switch contacts; the initial same-row pairing did not work.
- The ESP32-CAM with OV2640 and a USB adapter base connects to WiFi and has captured photos in `media/`. A live-stream firmware update compiled, but smoothness and stability still need further testing.
- Both test photos show horizontal lines and are dark. Investigate in better light, check the camera ribbon with USB disconnected, and try a known stable 5 V supply and cable. The cause is not yet confirmed.
- Obtain a second micro-USB cable so the button board can stay connected to the laptop while the camera is powered separately in the living room. Verify the camera's power adapter provides 5 V and ideally at least 2 A.
- Place the fixed camera in the living room and verify power, WiFi coverage, live view, and photo capture from the bedroom. Check whether any freeze affects only video or the entire camera page.
- Next integrate the button with the laptop so pressing Ver opens the camera view, then save a requested photo automatically under `media/`. The current photo button downloads through the browser instead.
- Add the servo search loop after the fixed-camera test works; move microphone and speaker to the gadget later. WiFi credentials belong only in the ignored local `esp32/camera/include/wifi_credentials.h` file.

## Vision test progress (SAM 3.1)
- `scripts/find_object.py <image> "<noun phrase>"` sends a local image to `sam-3.1` on the Meta Model API through the OpenAI Python SDK (`base_url=https://api.meta.ai/v1`), non-streaming, with `include_confidence: "true"`. The key is read from `SAM_API_KEY` in the ignored root `.env` file.
- It parses only the `<|box;...|>` tokens with a regex (masks are ignored); an empty output means no match. When found, it prints the box, confidence, and left/center/right position, and saves the image with boxes drawn to `media/output/`.
- Verified on `media/camera-test-2.jpg` with "white mouse": 1 match, box (180, 182) -> (246, 313), confidence 0.77. The box frames the mouse correctly despite the dark, striped photo.
- Per Meta's docs, SAM gives geometry only (boxes and masks); captions and answers about a scene belong to Muse Spark. Planned split: Muse Spark extracts the target noun phrase from the spoken request, SAM decides found/not found per photo in the search loop, and Muse Spark writes the final Spanish answer from the photo where the object was found.
- Next: consider removing `horizontal_position()` once Muse Spark writes the answer, consider Meta's `meta-sam-parser` (>= 0.0.6 for confidence) instead of the regex, and wrap the script as the agent's `find_object` tool.

## Guidelines
- Keep it simple and demo-first.
- User-facing language is Spanish; code and docs are in English.
