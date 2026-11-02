"""Wait for the user to finish, then drain the current gateway before deployment."""
import argparse
import json
import os
import time
import urllib.error
import urllib.request


def request(path, method="GET"):
    base = os.environ["CONFIO_PUBLIC_URL"].rstrip("/")
    if not base.startswith("https://"):
        raise SystemExit("CONFIO_PUBLIC_URL must use HTTPS")
    req = urllib.request.Request(base + path, method=method,
          headers={"Authorization": "Bearer " + os.environ["CONFIO_ADMIN_TOKEN"]})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.resume:
        request("/admin/undrain", "POST")
        return
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        try:
            request("/admin/drain", "POST")
            print("Gateway idle and drained; deployment can proceed")
            return
        except urllib.error.HTTPError as error:
            if error.code != 409:
                raise SystemExit(f"Deployment guard HTTP {error.code}; refusing to interrupt") from None
        time.sleep(5)
    raise SystemExit("User still active; deployment cancelled")


if __name__ == "__main__":
    main()
