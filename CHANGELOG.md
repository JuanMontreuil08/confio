# Changelog

## 0.0.3.1 — 2026-11-02

- Fix the shared Arduino WebSocket authorization header: the library appends its own CRLF, so an extra terminator caused HTTP 101 upgrades to close immediately with protocol error 1002.
- Add a raw-upgrade regression against the gateway and credential-free WiFi/clock diagnostics for board bring-up.

## 0.0.3.0 — 2026-11-02

- Add a single-device Modal gateway that reuses the existing voice agent and all six tools over authenticated WiFi.
- Add WiFi firmware targets for the voice board and handheld camera, preserving the validated pins, buttons, volume and original USB/local targets.
- Keep restored photos in Supabase and conversation history in RAM; use Lima time on the cloud server.
- Terminate known calls on device transport loss, and never replay calls, SMS or partial voice turns on reconnect.
- Add CI checks and an opt-in Modal deployment workflow that waits for active interactions and reconnects both boards during updates.
- Document secrets, deployment, one-board-at-a-time uploads and rollback. Real hardware WiFi acceptance remains separate from automated checks.

## 0.0.2.0 — 2026-10-31

- Add standalone MAX98357A playback of the retained microphone WAV from ESP32 flash.
- Preserve the hardware-tested 80% PCM amplitude and repeat the sample without a laptop audio stream.
- Document speaker wiring, the intermittent VIN contact, and how to restore microphone capture.

## 0.0.1.0 — 2026-10-31

- Add the hardware-validated INMP441 push-to-talk firmware and local WAV receiver.
- Document the working wiring and retain the final voice sample.
- Remove temporary microphone diagnostics and failed capture artifacts.
