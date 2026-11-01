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

## Call button prototype
- Isolated on branch `codex/llamar-prototype`. The user verified both buttons on the protoboard: GPIO 26 (Call) prints `Llamar conectado`; GPIO 27 (Look) prints `Ver ejecutado`. Both use internal pull-ups to shared GND and independent 50 ms debouncing.
- `web/call/` contains a local frontend with Julio's supplied profile photo, a light rounded call card, start/stop and microphone-mute controls, timer and waves driven by received audio. No AI agent is connected yet.
- `scripts/call_preview.py` serves the frontend at `http://localhost:8765/` and reads the gadget's USB serial messages at 115200 baud using pyserial. On `Llamar conectado`, it prints the URL and opens the default browser. Run `python3 scripts/call_preview.py --live --to "+51XXXXXXXXX"` for real browser-to-phone audio; without `--live` it remains a safe visual preview. Clicking **Talk with your agent** in live mode asks for microphone permission and places one real call.
- Close PlatformIO Serial Monitor before running the bridge. Use `--port` when multiple USB boards are connected. See `web/call/README.md` for commands. The user confirmed the physical-button-to-browser call flow works.
- Stop the serial bridge before firmware upload too. During the first browser-audio test the user started PlatformIO upload while the bridge was running; both reported the same serial no-data/multiple-access error. The bridge was stopped and `lsof /dev/cu.usbserial-0001` showed no holders. The web can stay running with `--live --no-serial` during upload; restart with serial enabled only after upload succeeds. Do not claim firmware recovery until SUCCESS is confirmed.
- Recovery confirmed: the developer stopped the bridge, uploaded the existing gadget firmware with PlatformIO to `/dev/cu.usbserial-0001` and got SUCCESS with flash hashes verified, then started one live bridge instance. The test requires only the physical Call button and the web start button; do not tell the user to upload or open Serial Monitor again for each test.
- Verified: a simulated serial port delivers Call events to the HTTP status endpoint; Look does not open the browser. Browser checks passed for start, mute, stop, restart, timer and profile image at laptop and 320px mobile widths, without horizontal overflow. Run bridge tests with `python -m unittest discover -s scripts/tests -v` after installing `scripts/call-requirements.txt`.
- The user requested an English-only call frontend, overriding the general Spanish UI guideline for this screen. Keep it minimal: one large Julio heading, timer, portrait, End call/Mute controls, waveform and start button. No logo, preview badge, taglines, duplicate portrait name, visible status copy, footer or text below the start button. Motion includes a short entrance and damped waveform transitions; reduced motion stays static.
- The selected telephony provider is Telnyx. The account shows Verified. Outbound profile `Default` (ID `3081866635162682559`) has Peru (+51) enabled. The user approved purchase of voice number `+16064318045` for $2 initially and $1/month; it now shows Active. Voice API app `Confio Call Test` has connection ID `3081883062951216585` and the number assigned. The user confirmed the standalone outbound call test works.
- The user added their API key to `.env` themselves and explicitly asked agents not to read that file. Give them configuration values to add themselves. `scripts/telnyx_webhook.py` never reads `.env`; it verifies signed events using the ignored `.telnyx-public-key`. The user approved ngrok as the temporary public webhook intermediary. See `scripts/TELNYX.md` for the endpoint and restart commands. Signature tests and public health/unsigned rejection checks passed; real Telnyx event delivery remains untested.
- `scripts/telnyx_call.py <international-number>` loads `.env` only when the user runs it, and places one outbound call using `TELNYX_API_KEY`, `TELNYX_CONNECTION_ID`, and `TELNYX_FROM_NUMBER`. It normalizes a missing `+`, waits 30 seconds for an answer, caps answered duration at 30 seconds, and disables route retries on timeout; it never retries HTTP requests. Unit tests use fake credentials and mocked requests without reading `.env`. No actual dial was executed by the developer. This is a silent ringing/connectivity test.
- The user subsequently explicitly authorized the live server to load the API key into memory for WebRTC provisioning and token creation, without displaying the file or secrets. Do not inspect `.env` manually. `scripts/telnyx_browser.py` provisions/reuses credential connection `Confio Browser Test` (`3081890161844290628`), direct outbound (parking off), channel limit one, Default profile, and the purchased caller ID. Only IDs/expiry are stored in ignored `.telnyx-webrtc.json`; the per-device credential expires after 24 hours and is renewed on server startup. The SDK gets a login JWT through a local same-origin POST, never the API key or SIP password. Do not expose the frontend/token server through ngrok.
- Real provisioning and JWT generation passed. Ten Python tests and five Node tests passed, covering signature checks, serial delivery, token origin restrictions, provisioning and call lifecycle/cancellation. Visual idle and activity-panel checks passed on the laptop. The user confirmed real two-way audio, then confirmed the full flow works after checking the button, remote waves, microphone mute and hangup. Their supplied screenshot shows active two-way media and a completed call of about 31 seconds. SDK version 2.28.0 is vendored locally. The browser uses actual remote audio RMS for the waveform; mute affects the local microphone only. Activity logs currently show real SDK/media states, without AI or transcription events. Browser-only limits: 45 s to answer and 10 min answered; no redial. Browser crash/network loss still requires checking the provider for call termination.
- The repository is public. Never commit the real test destination or credentials. Live mode requires the destination through `--to`; documentation/tests use example digits. The next feature is voice request → Meta speech-to-text → Muse Spark → call tool. Moving microphone and speaker to the gadget comes later after soldering. Do not add recording, summaries or a database now.
- Before writing Telnyx integration code, notify the user. Preserve the current idle UI. The requested future active layout moves the call card to the upper-left, shows minimal English debug logs for transcription, model requests, tool execution and provider events, and drives the waveform from the remote caller's real audio. No recording, summaries or database in the current scope.

## INMP441 microphone capture

- `esp32/mic/` is a standalone push-to-talk capture. The user confirmed clear voice with direct female-to-female signal connections: SCK GPIO 18, WS GPIO 5, SD GPIO 22. VDD goes to 3V3; GND and L/R go to GND. The button uses GPIO 23 with an internal pull-up. Preserve this validated mapping.
- Run `python3 esp32/mic/record.py --port /dev/cu.usbserial-0001` from the repository root (adjust the port as needed). Hold the button to record and release to save a stereo 16 kHz, 16-bit PCM WAV under `media/output/`. Channel 1 contains voice; channel 2 is effectively silent. USB serial runs at 921600 baud. Stop the receiver and serial monitors before firmware upload.
- The user authorized this isolated local microphone capture; it is not integrated with calls, transcription, summaries or a database. Preserve the working capture behavior. Temporary diagnostics and earlier failed microphone WAVs were removed; only the final sample is retained. See `esp32/mic/README.md`.
- Earlier SCK contacts and trials on WS 19 / SD 21 produced zeros or corrupted audio. The final wiring works, but testing did not prove any ESP32 GPIO is damaged. Do not present the hardware cause as confirmed.

## MAX98357A speaker playback

- `esp32/speaker/` is a separate standalone playback project. It embeds channel 1 of the retained `media/output/voice-20261031-194442-058191.wav` as 16 kHz, 16-bit PCM; no additional sample files should be committed. Generated PCM/header and build artifacts are ignored.
- The user confirmed playback through the MAX98357A and 8-ohm, 3 W speaker at 80% PCM amplitude. Final wiring: BCLK to RX2/GPIO 16, LRC to TX2/GPIO 17, DIN to D19/GPIO 19; VIN directly to USB-fed ESP32 VIN (measured about 4.6 V), GND to common ground. SD and GAIN are unconnected on this breakout. Speaker purple (+) and gray (-) go only to amp outputs, never ESP32 GND.
- The VIN jumper was intermittent: pressing it restored sound. A firm physical power connection remains necessary; normal I2S logs cannot establish audible output. Do not describe the connector as permanently repaired.
- After uploading, playback starts automatically and repeats after five seconds of silence. USB supplies power only, and unplugging it stops the test. Uploading speaker firmware temporarily replaces mic/button firmware; it does not change mic wiring/source. Stop serial processes before uploading. See `esp32/speaker/README.md` for build, upload and mic-restore commands.
- Telnyx audio forwarding, WiFi streaming, Cartesia TTS and the agent are not integrated into this playback test. The user's final goal is a WiFi device powered by USB with cloud services, without a user laptop. Notify the user before writing Telnyx integration code.

## Guidelines
- Keep it simple and demo-first.
- User-facing language is Spanish; code and docs are in English.

## Voice loop and voice agent (branch `codex/voice-loop`)

- `esp32/voice/` combines the validated mic (I2S0: 18/5/22, button 23) and speaker (I2S1: 16/17/19) in one firmware over USB serial at 921600 baud; wiring is unchanged and `esp32/mic`/`esp32/speaker` stay untouched. Speaker PCM amplitude is 100%. Framed protocol: ESP32 sends `MIC1` S/D/F/A/I/E, laptop sends `SPK1` P (≤1024 bytes mono 16 kHz PCM) and E; the laptop keeps at most 8 frames in flight and waits for one `A` per frame. The button is ignored while audio plays. At boot the firmware reports `Mic check peak N`; `0` means no mic signal. See `esp32/voice/README.md`.
- `esp32/voice/voice_link.py` is the reusable laptop side (`record()`, `play()`, 3 s ack timeout, restart detection); `loopback.py` is the echo test (`--wav` plays a file). The user verified echo and WAV playback on hardware.
- Hardware is not yet reliable: the speaker went silent three times and the mic read exact zeros twice, each fixed by pressing jumpers (amplifier VIN/GND, a mic jumper). Software acks cannot detect an unpowered amp. Securing or soldering those connections is pending. To check the amp, loop `loopback.py --wav` while adjusting VIN.
- `scripts/voice_agent.py` runs one push-to-talk turn per press: ESP32 voice → Meta `muse-voice-transcribe-1.0` (`/v1/asr/transcribe`, multipart WAV, `languageBias: spanish`) → `muse-spark-1.3` on the Responses API → Cartesia `sonic-3.6` (`/tts/bytes`, `Cartesia-Version: 2026-08-14`, raw `pcm_s16le` 16 kHz, voice `b4b8e2af-6139-466e-a93a-30c20d2e1fc5`, language `es`) → ESP32 speaker. No conversation memory; each question is independent. Run `python3 scripts/voice_agent.py --port /dev/cu.usbserial-0001`; options `--model`, `--effort`, `--voice-id`.
- The user added `META_API_KEY` and `CARTESIA_API_KEY` to `.env` and authorized the script to load them into memory; never display them or inspect `.env` manually.
- The system prompt lives in `prompts/assistant_system.md` (English, edited by the user): Spanish answers, one to three short sentences, no lists/markdown, caution on health/money/emergencies, and always call the datetime tool. It is read once at startup, so restart the agent after editing it.
- Tool calling follows Meta's docs: `tool_choice` only supports `"auto"`, so the prompt instructs tool use. The only tool is `get_current_datetime` (no arguments; laptop local ISO time and English weekday). Arguments are validated, errors are returned to the model, and the loop is capped at 3 tool rounds. Meta's only built-in tools are `web_search` and `tool_search`; there is no built-in time tool. Meta's tool docs only show Spark, so Spark replaced Wonder as the default.
- Latency: `previous_response_id` (server-stored state) made the post-tool call take 10–23 s and produced "persistence service error" 503s. Stateless calls (`store: false`, `include: ["reasoning.encrypted_content"]`, resending the turn with the model's output items) brought Spark to about 5–8 s. Reasoning effort is `medium` by user choice (`none` is rejected by Spark; `low` was only slightly faster). A measured voice turn: transcribe 1.6 s, Spark 7.6 s with the tool, Cartesia 1.7 s, about 11 s from button release to audio.
- Each run writes `logs/voice-agent-<start>.log` (timings, transcript, tool calls, answer, errors) plus `turn-NN-voice.wav`/`turn-NN-answer.wav`; `logs/` is ignored because the repository is public. Errors in a turn are logged and the agent keeps listening.
- Tests: `python3 -m unittest discover -s scripts/tests -v` (fake serial and fake APIs; no `.env`, no network). Next ideas, not started: streaming LLM/TTS to cut latency, conversation memory, camera/SAM tools, WiFi transport for the power bank.
