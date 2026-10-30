# Local Telnyx call test setup

The Voice API application **Confio Call Test** has connection ID
`3081883062951216585`. It uses outbound profile **Default**, with Peru (+51)
enabled, and the active number `+16064318045`.

Add these values to your ignored root `.env` yourself, alongside your API key:

```dotenv
TELNYX_CONNECTION_ID=3081883062951216585
TELNYX_FROM_NUMBER=+16064318045
```

The webhook receiver does not read `.env`, use an API key, or place calls.
It verifies Telnyx Ed25519 signatures with the account public key stored in the
ignored root `.telnyx-public-key` file. That key comes from the portal's Public
Key page, and is different from the secret API key.

## Run the webhook

From the repository root, in separate terminals:

```sh
python3 -m pip install -r scripts/telnyx-requirements.txt
python3 scripts/telnyx_webhook.py
```

```sh
ngrok http 8766
```

The configured public endpoint for this session is:

```text
https://taylor-hypochloremic-emogene.ngrok-free.dev/webhooks/telnyx
```

Keep both processes running while testing. After restarting ngrok, check its
displayed URL; if it changes, update the application's webhook URL in Telnyx.
Stop each process with Ctrl+C when finished.

The tunnel forwards requests only to this receiver on port 8766. Signed events
are logged as JSON metadata (event type, event ID, call control ID and hangup
reason), without full payloads or phone numbers. Duplicate event IDs are
acknowledged without logging again. No audio or recordings are processed.

`GET /healthz` checks availability. An unsigned request to
`POST /webhooks/telnyx` should return 403.

## Place one test call

With the webhook and ngrok running, install the requirements above and run:

```sh
python3 scripts/telnyx_call.py "+51XXXXXXXXX"
```

The destination is a positional argument; replace it to test another number.
Include the country code, with or without a leading `+`. This command places
one real, billable call from `TELNYX_FROM_NUMBER`. The script loads the ignored
root `.env` only when executed (environment variables take precedence).

It waits up to 30 seconds for an answer and limits the answered call to 30
seconds. There are no automatic HTTP retries or route retries after timeout.
This is a ringing/connectivity test: the callee hears silence because browser
audio and the agent are not connected yet. The callee can hang up normally.
The script exits after the dial response; exiting it does not end the call.
Watch the webhook terminal for `call.answered` and `call.hangup`.
An accepted API request alone does not prove the destination answered. If a
network error occurs, inspect Telnyx before running again to avoid duplicates.

API reference: [Telnyx Dial](https://developers.telnyx.com/api-reference/call-commands/dial).

## Validation and remaining work

```sh
python3 -m unittest discover -s scripts/tests -p 'test_telnyx_webhook.py' -v
python3 -m unittest discover -s scripts/tests -p 'test_telnyx_call.py' -v
```

Signature, expiry, tampering, duplicate and malformed-event checks pass.
Public health and rejection of unsigned requests were checked through ngrok.
The user confirmed that the standalone outbound call worked.
The dial script is tested with mocked HTTP requests and fake credentials only;
the developer did not load `.env` or execute a real call for that script.

## Browser audio test

The browser test now uses the SDK with a separate credential connection,
**Confio Browser Test** (`3081890161844290628`), without call parking. Run
`python3 scripts/call_preview.py --live --to "+51XXXXXXXXX"`.
The user explicitly authorized this live server to use the API key in memory
for provisioning and token creation, without displaying `.env`. Real provisioning
and JWT generation passed. The user confirmed a real browser call rings the
destination and both sides hear each other, then confirmed the complete flow,
including the physical button, remote waves, microphone mute and hangup.
See [browser call instructions](../web/call/README.md) for the full button-to-web
flow, microphone mute, hangup and remote audio waves. This direct SDK flow does
not depend on ngrok or the Voice API webhook to carry audio; ngrok remains useful
for the separate Voice API test. Agent integration is still pending.
