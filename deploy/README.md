# Confio on Modal

One Python service, one user, two authenticated outbound WiFi connections.
The existing `Agent`, prompt, tools, voice and pin mappings are reused. Hold
button 1 to speak/release to send; button 2 cancels or hangs up. No new screen,
app, model router or conversation database is introduced.

## Architecture

- `/device`: binary `MIC1` / `SPK1` audio and button frames over WSS. I2S speaker
  work runs separately from networking so microphone and cancel stay responsive.
  A bounded acknowledgement window paces playback. Partial turns are discarded
  on disconnect; calls/SMS/captures are never replayed on reconnection.
- `/camera`: an authenticated WSS connection initiated by the camera. A capture
  command contains a random request ID; the camera drains stale buffers and
  replies with that ID and JPEG. Photos still go through Muse Image before
  Supabase Storage and the existing Telnyx SMS tool.
- `/webhooks/telnyx` and `/media/{token}`: the existing signed event receiver
  and bidirectional call bridge, using the Modal URL instead of ngrok.
- `/healthz`: public liveness only. `/admin/status`, `/admin/drain` and
  `/admin/undrain` require a separate deployment token. Provider keys and
  contacts are injected with Modal Secrets, never bundled in an image or board.

The event loop owns sockets; one worker executes the synchronous agent/tools.
`max_containers=1` keeps both boards and Telnyx in the same runtime;
`modal.concurrent(max_inputs=16)` lets the simultaneous connections be served.
This is intentionally a single-device deployment.

Supabase remains the existing private photo store. Conversation history and
photo/SMS references stay in RAM with the existing five-minute idle expiry.
They survive a board reconnect in the same runtime, but not a container restart
or deployment. Saved Supabase files survive. Transient audio/photos use a
container-local temporary directory; no new permanent audio store is added.

## Configure and deploy

1. Follow [SECRETS.md](SECRETS.md). Authenticate using `modal token new` yourself.
2. Run from the repository root:

```sh
python3 -m venv .venv-modal
.venv-modal/bin/python -m pip install -r deploy/tooling-requirements.txt
MODAL_CONFIG_PATH="$PWD/.modal-local/config.toml" .venv-modal/bin/modal deploy deploy/modal_app.py
```

The current gateway URL is `https://juanmontreuil71--confio-gateway.us-east.modal.run`.
Set `CONFIO_PUBLIC_URL` to that exact base URL. After editing a Modal Secret,
roll over the service while idle so new containers load the values:

```sh
MODAL_CONFIG_PATH="$PWD/.modal-local/config.toml" .venv-modal/bin/modal app rollover confio
```

## Flash boards, one at a time

WiFi uses the same validated I2S pins, volume and buttons. Both projects keep
their original default targets for USB/local mode.

- Camera WiFi stays in ignored `esp32/camera/include/wifi_credentials.h`.
- The voice board's ignored `wifi_credentials.h` may include the camera's header
  to share the same network without duplicating its password.
- Copy each `cloud_config.example.h` to ignored `cloud_config.h`. Set the gateway
  hostname (no scheme/path) and that board's token. Never put provider keys there.
- Use 2.4 GHz WiFi with internet, without a captive login page. Certificates are
  validated against the public roots in `esp32/common/cloud_roots.h`; time sync
  is required. No insecure TLS fallback is provided.

Stop the serial agent/monitors before uploads. Connect only the voice board:

```sh
~/.platformio/penv/bin/pio run -d esp32/voice -e esp32wifi -t upload --upload-port /dev/cu.usbserial-0001
```

Then disconnect it, power it by USB/power bank, and connect the camera:

```sh
~/.platformio/penv/bin/pio run -d esp32/camera -e esp32camwifi -t upload --upload-port /dev/cu.usbserial-0001
```

Return the camera to its power bank. Both boards reconnect automatically.
Power must remain stable under WiFi and loud speaker output. Software ACKs
cannot establish that an amplifier is powered or audible.

To return to the previous behavior, upload voice target `esp32dev` and camera
target `esp32cam`, then run the existing USB voice agent. Standalone mic and
speaker projects remain untouched.

## CI/CD

[The workflow](../.github/workflows/modal.yml) checks Python/Node regressions
and compiles both WiFi firmwares with example credentials. It deploys only
pushes to `main`, after checks pass. Configure the GitHub Actions
secrets/variable in [SECRETS.md](SECRETS.md). PRs run checks without deployment
credentials. Never publish real firmware binaries: they embed WiFi/device tokens.

Before an update, the workflow waits up to 15 minutes for recording, model/tool
work, playback or a call to finish. It then drains **both** board connections;
they reconnect after the new version is available. Updates reset RAM history.
If deployment fails, the workflow undrains the prior service. Firmware changes
still require a USB upload; CI/CD updates the server only.

## Resources and verification

The service requests 0.5 CPU and 512 MiB RAM, with no GPU and at most one
container per deployment. Connected WebSockets keep work active and billable;
`min_containers=0` does not make connected idle devices free. Set a spending
limit/budget in your Modal account as available. Do not assume the $30 credit
covers unlimited use. Meta/Cartesia/Telnyx/Supabase billing remains separate.

Run the existing mocked suite with `python -m unittest discover -s scripts/tests -q`.
Cloud tests additionally cover auth separation, bounded audio, speaker ACKs,
partial-turn disconnects, camera correlation, deployment draining and call
termination after transport loss. Hardware acceptance is a short check of one
voice answer, a call/cancel and photo/SMS without the laptop agent running.

The user has now verified real WiFi spoken answers, internet search, phone
calling and the photo flow with the camera powered by a power bank. Logs confirm
capture, Muse Image restoration, Supabase storage and queued photo SMS. WiFi
button-2 cancellation was not separately confirmed. Physical mic/amp contacts
still need to remain secure; playback acknowledgements alone do not prove sound.

## Next functionality (not implemented)

- Send a voice-dictated text SMS to a saved contact without requiring a photo.
- Include user-dictated text alongside a photo's signed link in the same SMS.
- Preserve the current voice/button interaction, saved-contact restrictions and
  the rule against automatically retrying sends with an unknown outcome.

Currently `send_photo_sms` accepts only `photo_id` and `contact`, uses a fixed
caption, and there is no general text-SMS tool. This follow-up is documented for
a later change; it is not part of the current implementation.

References: [Modal WebSockets](https://modal.com/docs/guide/webhooks),
[concurrency](https://modal.com/docs/guide/concurrent-inputs),
[Secrets](https://modal.com/docs/guide/secrets),
[deployment](https://modal.com/docs/guide/continuous-deployment),
[scaling](https://modal.com/docs/guide/scale),
[timeouts](https://modal.com/docs/guide/timeouts).
