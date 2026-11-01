# Voice photo sharing

The existing voice button controls a handheld ESP32-CAM powered separately by
a power bank. Both the camera and laptop must be on the same WiFi network.
No LCD, SAM, new firmware or separate conversation service is required.

```text
voice -> Meta transcription -> Spark -> ask when ready
next voice -> Spark -> take_photo (camera + Muse Image)
                    -> save_photo (Supabase)
                    -> send_photo_sms (Telnyx link)
                    -> Spark response -> Cartesia -> existing speaker
```

## Local configuration

Add these values to the ignored root `.env` yourself. Never commit keys, real
destinations or signed photo URLs. The agent loads settings at startup; restart
after editing them. The code never needs a database password.

```dotenv
CAMERA_URL=http://192.168.X.X
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_SECRET_KEY=YOUR_SERVER_SECRET_KEY
SUPABASE_STORAGE_BUCKET=photos
TELNYX_MESSAGING_PROFILE_ID=YOUR_MESSAGING_PROFILE_ID
TELNYX_SMS_SENDER=Confio
```

The existing `META_API_KEY`, `CARTESIA_API_KEY` and `TELNYX_API_KEY` are reused.
`SUPABASE_SECRET_KEY` accepts a modern `sb_secret_...` key, or a legacy server
`service_role` JWT. Use a private bucket named by `SUPABASE_STORAGE_BUCKET`;
create it in the Supabase dashboard before starting. Keys stay on the laptop.

Recipients come only from the existing ignored `contacts.json`, for example:

```json
{"Naty": "+51999999999"}
```

Replace example digits locally. Names match regardless of case or accents.
The tool descriptions expose names, never numbers, keys or signed URLs.

## Run

Stop any existing agent or serial monitor using the gadget port, then:

```sh
python3 -m pip install -r scripts/voice-agent-requirements.txt
python3 scripts/voice_agent.py --port /dev/cu.usbserial-0001
```

Photo tools are enabled when `CAMERA_URL` is set. Without it, the normal voice
agent remains available. Calls still require the existing `--public-url` setup
in `AGENTS.md`; no Telnyx voice provisioning or call firmware is changed here.
The SMS flow does not require ngrok.

1. Hold the button: “Quiero tomar una foto y mandársela a Naty.” Release it.
2. Hear the agent ask you to aim and say when ready.
3. Aim the camera, hold the button again: “Ya, tómala ahora.” Release it.
4. The agent captures, restores, saves and sends. Wait for its spoken response.
5. Ask “¿Ya llegó?” to check the actual SMS status without resending.

Spark chooses tools from the conversation, not keyword matching. If no recipient
was requested, it can save the photo and ask who should receive it. Each voice
turn allows one capture/restoration attempt. Once a photo/contact SMS has been
attempted, repeated requests check its status rather than send another message.
A network timeout during send is treated as unknown, not proof of failure or
success, and is never automatically retried. Take a new photo to make a new send.
Within capture, the first two camera buffers are discarded before using the
third frame: the current firmware can otherwise return old frames after idle.

## Photos, links and history

Original and restored files are retained locally under ignored
`logs/voice-agent-<start>/photos/<photo_id>/`. Supabase receives
`<photo_id>/original.jpg` and `<photo_id>/restored.jpg`. A seven-day signed link
to the restored image is sent inside an ordinary SMS, without MMS attachments.
Signed links can span multiple SMS parts, billed by Telnyx; no new pricing or
voice settings are configured by the script.

The model receives opaque photo IDs and actual tool results. It does not need
to receive photo bytes to orchestrate storage and sending. Restoration failures
stop the pipeline; the unmodified local original is retained. No un-restored
substitute is sent automatically. A model edit is not guaranteed to preserve
every detail: the restoration prompt asks it to preserve the scene and labels.

History uses Meta's stateless Responses replay (`store: false` plus encrypted
reasoning items). Messages, complete model output and tool results survive button
presses. Call completion is also added to history; queued SMS is not presented
as delivered. Pending delivery is refreshed at the next valid voice request.

Five minutes of idle waiting ends the conversation. Expiration is checked using
the start time of the next recording, before its request enters the model. The
clock starts after all processing, playback and phone-call announcements finish;
it cannot interrupt an active action. Context and in-memory photo references are
reset together. Stored photos are not deleted. No automatic history trimming,
database conversation storage or background delivery monitor is added.

## Verification

```sh
python3 -m unittest discover -s scripts/tests -v
```

Tests use fake camera, image, storage, messaging and speech services. They do not
read `.env`, contact real APIs, send SMS or dial numbers. Hardware and live API
validation remain separate from those tests.

References: [Meta image editing](https://dev.meta.ai/docs/image-generation),
[Responses history](https://dev.meta.ai/docs/protocols/responses),
[Supabase keys](https://supabase.com/docs/guides/getting-started/api-keys),
[Supabase signed links](https://supabase.com/docs/guides/storage/serving/downloads),
[Telnyx messaging](https://developers.telnyx.com/api-reference/messages/send-a-message).
