#!/usr/bin/env python3
"""Serve the call UI and bridge the gadget's existing USB serial messages."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import webbrowser

WEB_ROOT = Path(__file__).resolve().parents[1] / "web" / "call"
CALL_MESSAGE = "Llamar conectado"
USB_VENDORS = {0x10C4, 0x1A86, 0x0403, 0x303A}


class PreviewState:
    def __init__(self):
        self.lock = threading.Lock()
        self.connected = False
        self.call_event = 0

    def connection(self, connected):
        with self.lock:
            self.connected = connected

    def snapshot(self):
        with self.lock:
            return {"connected": self.connected, "call_event": self.call_event}

    def receive(self, line, url, open_browser=True):
        if line == "Ver ejecutado":
            print(line, flush=True)
        if line != CALL_MESSAGE:
            return False
        with self.lock:
            self.call_event += 1
        print(f"{CALL_MESSAGE}\nAbre la página: {url}", flush=True)
        if open_browser:
            try:
                webbrowser.open(url, new=0, autoraise=True)
            except webbrowser.Error as error:
                print(f"Abre el enlace manualmente: {error}", flush=True)
        return True


class PreviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, state, service=None, **kwargs):
        self.state = state
        self.service = service
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def reply(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in (f"localhost:{port}", f"127.0.0.1:{port}")

    def do_GET(self):
        if not self.valid_host():
            self.reply(403, {"error": "Local access only"})
            return
        if self.path == "/api/config":
            self.reply(200, {"live": self.service is not None})
            return
        if self.path == "/api/status":
            body = json.dumps(self.state.snapshot()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()

    def do_POST(self):
        # Browser-only same-origin JSON requests; no CORS or public tunnel here.
        origin = "http://" + self.headers.get("Host", "")
        if (not self.valid_host() or self.headers.get("Origin") != origin
                or self.headers.get("Content-Type") != "application/json"
                or self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin"):
            self.reply(403, {"error": "Same-origin access required"})
            return
        if self.path != "/api/session":
            self.reply(404, {"error": "Not found"})
            return
        if not self.service:
            self.reply(503, {"error": "Start the server with --live to connect Telnyx."})
            return
        try:
            self.reply(200, self.service.session())
        except Exception:
            # Never expose provider responses or credentials to the frontend.
            self.reply(502, {"error": "Could not authenticate with Telnyx. Check the local setup."})

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(self)")
        super().end_headers()

    def list_directory(self, path):
        self.send_error(404)

    def log_message(self, format, *args):
        # Keep the terminal readable for the physical button events.
        pass


def watch_serial(state, url, requested_port, open_browser, shutdown):
    try:
        import serial
        from serial.tools import list_ports
    except ImportError:
        print("La web está lista. Para usar el botón, instala: python3 -m pip install -r scripts/call-requirements.txt", flush=True)
        return

    last_notice = None
    while not shutdown.is_set():
        candidates = [p.device for p in list_ports.comports() if p.vid in USB_VENDORS]
        port = requested_port or (candidates[0] if len(candidates) == 1 else None)
        if port is None:
            notice = ("Conecta el ESP32 DevKit por USB. Esperando el botón Llamar…" if not candidates
                      else "Hay varias placas conectadas. Reinicia con --port " + " o ".join(candidates))
            if notice != last_notice:
                print(notice, flush=True)
                last_notice = notice
            shutdown.wait(2)
            continue
        try:
            with serial.Serial(port, 115200, timeout=1) as device:
                state.connection(True)
                print(f"Botones conectados: {port} · 115200 baudios", flush=True)
                last_notice = None
                while not shutdown.is_set():
                    line = device.readline().decode("utf-8", errors="replace").strip()
                    state.receive(line, url, open_browser)
        except (serial.SerialException, OSError) as error:
            notice = f"No se pudo leer {port}. Cierra el monitor serial de PlatformIO. Reintentando… ({error})"
            if notice != last_notice:
                print(notice, flush=True)
                last_notice = notice
        finally:
            state.connection(False)
        shutdown.wait(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Serial port of the gadget; otherwise auto-detect one USB board")
    parser.add_argument("--http-port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="Print URL on press without opening the browser")
    parser.add_argument("--no-serial", action="store_true", help="Preview the UI without a connected gadget")
    parser.add_argument("--live", action="store_true", help="Enable Telnyx browser audio using the local .env")
    parser.add_argument("--to", help="Test destination with country code (required with --live)")
    args = parser.parse_args()
    if args.live and not args.to:
        parser.error("Usa --to con el número destino y su código de país.")
    state = PreviewState()
    shutdown = threading.Event()
    service = None
    if args.live:
        from telnyx_browser import BrowserService
        try:
            service = BrowserService.from_local_env(args.to)
            service.setup()
        except Exception as error:
            # ProviderError messages contain only sanitized status information.
            from telnyx_browser import ProviderError
            message = str(error) if isinstance(error, ProviderError) else "Revisa la configuración WebRTC local."
            parser.exit(1, message + "\n")
    handler = partial(PreviewHandler, state=state, service=service)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.http_port), handler)
    except OSError as error:
        parser.exit(1, f"No se pudo iniciar la web: {error}. Prueba --http-port 8766.\n")
    url = f"http://localhost:{server.server_port}/"
    mode = "Telnyx audio listo; la llamada comienza al hacer clic." if service else "Vista previa sin audio."
    print(f"Página de Julio: {url}\n{mode}\nCtrl+C para detener.", flush=True)
    if not args.no_serial:
        threading.Thread(target=watch_serial, args=(state, url, args.port, not args.no_open, shutdown), daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\nVista previa detenida.", flush=True)
    finally:
        shutdown.set()
        server.server_close()


if __name__ == "__main__":
    main()
