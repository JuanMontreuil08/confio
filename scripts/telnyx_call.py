#!/usr/bin/env python3
"""Place one short outbound Telnyx test call to a command-line destination."""

import argparse
import json
import os
from pathlib import Path
import re
import ssl
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import certifi
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def phone_number(value):
    """Accept international digits with an optional leading plus."""
    value = value.strip()
    if not value.startswith("+"):
        value = "+" + value
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", value):
        raise argparse.ArgumentTypeError("Usa el código de país, por ejemplo +51999999999.")
    return value


def dial(api_key, connection_id, source, destination):
    payload = {
        "connection_id": connection_id,
        "from": source,
        "to": destination,
        "command_id": str(uuid4()),
        "timeout_secs": 30,
        "retry_on_timeout": False,
        "time_limit_secs": 30,
    }
    request = Request(
        "https://api.telnyx.com/v2/calls",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    # Exactly one request: never automatically retry a potentially billable call.
    with urlopen(request, timeout=20, context=ssl.create_default_context(
        cafile=certifi.where()
    )) as response:
        return json.load(response)["data"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("number", type=phone_number, help="Destino con código de país")
    args = parser.parse_args(argv)
    load_dotenv(ROOT / ".env", override=False)
    values = {key: os.environ.get(key, "").strip() for key in (
        "TELNYX_API_KEY", "TELNYX_CONNECTION_ID", "TELNYX_FROM_NUMBER"
    )}
    missing = [key for key, value in values.items() if not value]
    if missing:
        parser.error("Falta configurar: " + ", ".join(missing))
    try:
        source = phone_number(values["TELNYX_FROM_NUMBER"])
    except argparse.ArgumentTypeError:
        parser.error("TELNYX_FROM_NUMBER debe ser un número internacional válido.")

    print(f"Llamando desde {source} a {args.number}…", flush=True)
    print("Prueba de timbrado: sin audio; máximo 30 s después de contestar.", flush=True)
    try:
        data = dial(values["TELNYX_API_KEY"], values["TELNYX_CONNECTION_ID"], source, args.number)
    except HTTPError as error:
        # Never print headers, credentials or the raw provider response.
        print(f"Telnyx rechazó la solicitud (HTTP {error.code}). Revisa los logs del portal.", file=sys.stderr)
        return 1
    except (URLError, TimeoutError, OSError):
        print("No se pudo confirmar la respuesta. Revisa Telnyx antes de repetir: "
              "la llamada podría haberse iniciado.", file=sys.stderr)
        return 1
    except (ValueError, KeyError, TypeError):
        print("Respuesta inesperada. Revisa Telnyx antes de repetir la llamada.", file=sys.stderr)
        return 1
    print("Solicitud aceptada; esto aún no confirma que el teléfono haya contestado.")
    print(f"Call control ID: {data.get('call_control_id', '(no disponible)')}")
    print("Revisa call.answered / call.hangup en la terminal del webhook.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
