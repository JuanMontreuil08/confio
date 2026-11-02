# Modal configuration

Create one **Custom Secret** named `confio` in the Modal `main` environment.
Enter the values yourself; do not paste them into chat or commit them.

Copy these existing settings from your local configuration:

```text
META_API_KEY
CARTESIA_API_KEY
TELNYX_API_KEY
TELNYX_CONNECTION_ID
TELNYX_FROM_NUMBER
SUPABASE_URL
SUPABASE_SECRET_KEY
SUPABASE_STORAGE_BUCKET
TELNYX_MESSAGING_PROFILE_ID
TELNYX_SMS_SENDER
```

Add:

- `TELNYX_PUBLIC_KEY`: the base64 public key currently in `.telnyx-public-key`.
- `CONTACTS_JSON`: the entire contents of your private `contacts.json`, as one JSON value.
- `CONFIO_DEVICE_TOKEN`: a random token of at least 32 characters for the voice board.
- `CONFIO_CAMERA_TOKEN`: a **different** random token of at least 32 characters for the camera.
- `CONFIO_ADMIN_TOKEN`: a **third** random token of at least 32 characters, used only by deployment automation.
- `CONFIO_PUBLIC_URL`: the deployed gateway's `https://...modal.run` URL, without a trailing slash. We can add this after the first deploy returns its URL, before connecting boards.
- `CONFIO_TIMEZONE`: `America/Lima`.

Generate each token locally with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`.
Provider keys remain server-side. Only the corresponding board token and WiFi
credentials go into each board's ignored headers. Do not use `CAMERA_URL` for cloud mode.

## GitHub Actions

Add these repository Actions secrets:

- `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`: a Modal deployment token.
- `CONFIO_ADMIN_TOKEN`: the same admin token from the `confio` Modal Secret.

Add repository Actions variable `CONFIO_PUBLIC_URL` after the first deployment.
Set repository Actions variable `CONFIO_DEPLOY_ENABLED` to `true` only after
the tokens are configured. Until then, pushes run checks without attempting a deploy.
Use a dedicated deployment token with an appropriate lifetime; expiration only
disables future CI/CD deployments, not an already-running gateway.
The workflow deploys relevant pushes to `main` after checks pass; pull requests
only run validation. Manual runs deploy only when launched against `main`.
