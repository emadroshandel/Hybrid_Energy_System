#!/usr/bin/env python3
"""
HES local / network server.

Serves the web interface and the JSON API from the standard library alone -
no Flask, no install step. Run it and open the printed address.

    python server.py                  # localhost only, opens a browser
    python server.py --network        # also reachable from the LAN
    python server.py --port 8080      # choose the port
    python server.py --no-browser     # do not open a browser

Network mode binds 0.0.0.0 so colleagues on the same network can use the
tool. It prints every address it is reachable on, because guessing which
interface a machine will be found on is a waste of everyone's time.

A word on what network mode is not: there is no authentication here, and
anything reachable on the LAN is reachable by everyone on the LAN. That is
appropriate for a design office and inappropriate for the open internet.
The server refuses to bind a public interface without --network being given
explicitly, so it cannot happen by accident.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HERE, "web")
sys.path.insert(0, HERE)

from ensys import api, __version__  # noqa: E402

MAX_BODY = 64 * 1024 * 1024   # 64 MB, enough for a pasted 8760-row CSV

# Content types are stated here rather than asked of the operating system.
# On Windows, mimetypes consults the registry, where a stray entry can map
# .js to text/plain; served with X-Content-Type-Options: nosniff - which is
# right to send - the browser then refuses to execute the script and the
# application never starts, with nothing in the interface to say why.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".py": "text/x-python; charset=utf-8",
    ".map": "application/json; charset=utf-8",
}


def content_type_for(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in CONTENT_TYPES:
        return CONTENT_TYPES[ext]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


class Handler(BaseHTTPRequestHandler):
    server_version = f"HES/{__version__}"
    protocol_version = "HTTP/1.1"

    # -------------------------------------------------------------- helpers

    def _send(self, code, body, content_type="application/json",
              extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # The UI is served from the same origin, so no CORS is needed; not
        # sending permissive CORS headers is deliberate.
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=api._json_default), "application/json")

    def log_message(self, fmt, *args):
        if self.server.verbose:
            sys.stderr.write(
                f"  {self.address_string()} {fmt % args}\n"
            )

    # ------------------------------------------------------------------ GET

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            path = "/index.html"

        if path == "/api/health":
            # The identifying fields matter when something is already
            # answering on this port: they say whether it is this
            # installation or a server left running from another folder.
            return self._json({
                "ok": True,
                "version": __version__,
                "uptime_s": time.time() - self.server.started,
                "pid": os.getpid(),
                "root": HERE,
                "python": sys.version.split()[0],
            })

        # Every action is also reachable by GET, with the payload as a query
        # parameter. Some local security software inspects and occasionally
        # stalls POST requests to loopback; when that happens the interface
        # retries the same call this way rather than hanging. Payloads that
        # do not fit in a URL still have to go by POST.
        if path.startswith("/api/"):
            action = path[len("/api/"):]
            raw = ""
            if "?" in self.path:
                from urllib.parse import parse_qs, urlparse

                raw = (parse_qs(urlparse(self.path).query).get("payload")
                       or [""])[0]
            try:
                payload = json.loads(raw) if raw else {}
            except ValueError as e:
                return self._json(
                    {"ok": False, "error": f"Invalid JSON payload: {e}"}, 400
                )
            result = api.handle(action, payload)
            return self._json(result, 200 if result.get("ok") else 400)

        # Static files, confined to the web directory.
        rel = path.lstrip("/")
        target = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not target.startswith(WEB_DIR):
            # Path traversal attempt; refuse without explaining the layout.
            return self._send(403, "Forbidden", "text/plain")
        if not os.path.isfile(target):
            return self._send(404, f"Not found: {path}", "text/plain")

        with open(target, "rb") as f:
            data = f.read()
        return self._send(200, data, content_type_for(target))

    # ----------------------------------------------------------------- POST

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/"):
            return self._send(404, "Not found", "text/plain")

        action = path[len("/api/"):]
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self._json({"ok": False, "error": "Bad Content-Length"}, 400)
        if length > MAX_BODY:
            return self._json(
                {"ok": False,
                 "error": f"Request body exceeds {MAX_BODY // 1048576} MB."},
                413,
            )

        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError as e:
            return self._json(
                {"ok": False, "error": f"Invalid JSON: {e}"}, 400
            )

        result = api.handle(action, payload)
        return self._json(result, 200 if result.get("ok") else 400)


def local_addresses():
    """Every address this machine is likely reachable on."""
    addrs = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            addrs.add(info[4][0])
    except Exception:
        pass
    try:
        # The usual trick: no packet is sent, but the OS picks the interface
        # it would route through, which is the one colleagues can reach.
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        addrs.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    addrs.discard("127.0.0.1")
    return sorted(addrs)


def find_free_port(start, host, attempts=20):
    """Take the requested port, or the next free one."""
    for p in range(start, start + attempts):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, p))
            s.close()
            return p
        except OSError:
            continue
    raise SystemExit(
        f"No free port between {start} and {start + attempts - 1}. "
        f"Something else is using them; pass --port to choose another range."
    )


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="HES hybrid energy system sizing - local server"
    )
    ap.add_argument("--port", type=int, default=8756)
    ap.add_argument(
        "--network", action="store_true",
        help="bind all interfaces so the tool is reachable from the LAN",
    )
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    host = "0.0.0.0" if args.network else "127.0.0.1"
    port = find_free_port(args.port, host)

    if not os.path.isdir(WEB_DIR):
        raise SystemExit(
            f"The web directory is missing: {WEB_DIR}\n"
            f"Run this script from inside the project folder."
        )

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.verbose = args.verbose
    httpd.started = time.time()
    httpd.daemon_threads = True

    url = f"http://127.0.0.1:{port}/"
    print(f"\n  HES {__version__}")
    print(f"  {'-' * 46}")
    print(f"  Local:    {url}")
    if args.network:
        for a in local_addresses():
            print(f"  Network:  http://{a}:{port}/")
        print(
            "\n  Network mode is on. Anyone on this network can reach the\n"
            "  tool; there is no password. Use it on a trusted network only."
        )
    else:
        print("  (use --network to share with others on your LAN)")
    print(f"  {'-' * 46}")
    print("  Ctrl+C to stop\n")

    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.\n")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
