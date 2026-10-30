# Browser call test

A local English call UI for the gadget's Call button. The idle design stays
minimal. Starting a call moves the card to the left, with remote-voice waves
above an activity panel. No AI agent, recording, transcript or database is used.

## Run

From the repository root:

```sh
python3 -m pip install -r scripts/call-requirements.txt -r scripts/telnyx-requirements.txt
python3 scripts/call_preview.py --live --to "+51XXXXXXXXX"
```

Open **http://localhost:8765/** and keep the server running. It binds only to
127.0.0.1. Close PlatformIO Serial Monitor before using the serial bridge.

The live server reads `TELNYX_API_KEY` and `TELNYX_FROM_NUMBER` from the ignored
root `.env` in memory. It provisions/reuses a separate credential connection
called **Confio Browser Test** using the Peru-enabled Default outbound profile.
Its outbound channel limit is one and call parking is disabled: the SDK calls
the phone directly. The purchased number remains assigned to the original
Voice API app; it is also used as this connection's outbound caller ID.

Connection/credential IDs and expiry are stored in ignored `.telnyx-webrtc.json`.
No secret is stored there. A per-device telephony credential expires after
24 hours; restart the live server to renew when needed. The browser receives
only a login JWT (Telnyx's API tokens last 24 hours), not the API key or SIP
password. Token responses are uncached and require same-origin JSON POSTs.
The server rejects foreign Host and Origin headers. Do not expose port 8765
through ngrok; this is a local development app, not a public authenticated service.

## Full test flow

1. Connect the gadget over USB. Existing firmware: Call GPIO 26 prints
   `Llamar conectado`; Look GPIO 27 prints `Ver ejecutado`, 115200 baud.
2. Press Call. The terminal prints the URL and opens the default browser.
   It does not automatically dial or request microphone access.
3. Click **Talk with your agent**, grant microphone access, and wait for the
   configured test destination to ring. This places a real billable call.
4. Answer on the phone. The laptop microphone goes to the phone, and the phone's
   audio plays through the browser. Use headphones to reduce echo, especially
   if both devices are nearby.
5. The waves use RMS intensity from the actual remote MediaStream, through a
   Web Audio analyser. The local microphone does not drive the waves. Reduced
   motion keeps the visual static while audio continues.
6. **Mute** silences the laptop microphone only. Remote audio stays audible.
   **End call** hangs up, disconnects the SDK and releases microphone tracks.
   Remote hangup also returns the UI to idle; activity logs remain for review.

The browser waits up to 45 seconds for an answer, and has a 10-minute test timer
while answered. These are browser-side limits, unlike the separate dial script's
provider-enforced 30-second limit. Closing the page attempts hangup; on a browser
crash or network loss, inspect the Telnyx portal to confirm the call ended.
There is no automatic redial. Activity logs show actual SDK and media lifecycle
states, without invented transcription or model/tool events.

Optional flags: `--port /dev/cu.usbserial-XXXX` selects the gadget when more than
one board is connected; `--no-open` only prints the URL; `--no-serial` enables
browser-only testing. Omit `--live` for a visual preview with no credentials or
calling. The preview button shows the activity layout but never simulates a
connected call. Stop with Ctrl+C before opening Serial Monitor or uploading.

## SDK and verification

`vendor/telnyx-2.28.0.js` is the official `@telnyx/webrtc@2.28.0` npm UMD bundle,
served locally with its MIT license. No CDN or frontend build step is needed.
See [Telnyx WebRTC fundamentals](https://developers.telnyx.com/docs/voice/webrtc/fundamentals)
and [JWT authentication](https://developers.telnyx.com/docs/voice/webrtc/auth/jwt).

```sh
python3 -m unittest discover -s scripts/tests -v
node --test scripts/tests/test_browser_call.mjs
```

Tests cover serial-to-HTTP delivery, same-origin token restrictions, provisioning,
call creation, duplicate-ready events, local mute, remote media, stop, remote
hangup, cancellation during permission requests and network loss. Tests use fake
credentials and mocked provider requests. Real provisioning and token generation
were checked. The user confirmed the complete physical-button-to-browser flow,
two-way audio, remote waves, microphone mute and hangup. All destination numbers
in this documentation are examples: replace them with your intended recipient.
