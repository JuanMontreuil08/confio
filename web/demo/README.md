# Confio browser demo

Run from the repository root with Python 3.12 or 3.13.

```sh
python3 -m venv .venv-demo
source .venv-demo/bin/activate
python -m pip install -r deploy/requirements.txt
python web/demo/server.py
```

Set `META_API_KEY` and `CARTESIA_API_KEY` in your environment or in the ignored
root `.env` before starting. The browser never receives these credentials.
Open [localhost:8080](http://localhost:8080). Hold the microphone button and speak, or use the text box or examples.

1. Ask for current news in Lima to exercise live web search.
2. Click **Prepare a photo**, then **Take photo**. The original ESP32 sample
   is visible before capture. Muse Image restores it and the demo shows both.
   To use your own photo, click **Use webcam**, allow camera access, frame
   the scene and click **Capture photo**. Only that captured frame is sent.
3. Click **Share photo**, or ask to send Juan an SMS with your own text.
   The simulated phone displays the text and photo together.
4. Ask to call Juan, then press **End call** to cancel the simulated call.

The browser replaces the ESP32 buttons, microphone and speaker. The camera
feeds an existing JPEG or a browser webcam capture into the same photo tool used in production. The
Responses API, transcription and speech synthesis use live services.
Photo saves are local to the session. The simulated phone never contacts
Telnyx or any real recipient.

To use an approved photo from private Supabase Storage, set
`CONFIO_DEMO_SAMPLE_OBJECT` to its bucket-relative path (for example
`example/original.jpg`) and supply `SUPABASE_URL`,
`SUPABASE_SECRET_KEY` and `SUPABASE_STORAGE_BUCKET` on the server. The server
downloads that exact object once at startup and displays it to demo visitors.
Use a photo approved for sharing. Otherwise the demo uses the ESP32 sample
already checked into the repository.

Set `CONFIO_DEMO_CALLER_ID` on the server to customize the caller ID shown in
the simulated banner. It defaults to a fictional number.

This entry point binds to localhost. For public judging, use a separate HTTPS
deployment with server-side secrets and a request budget. The physical device
and production deployment are documented in [the hardware guide](../../deploy/README.md).
