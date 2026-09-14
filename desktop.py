#!/usr/bin/env python3
"""
HES desktop window.

Runs the local server in a background thread and shows it in a native window
via pywebview, so the tool behaves like an application rather than a browser
tab.

    python desktop.py

pywebview is the only optional dependency in the whole project, and it is
only needed for this file. If it is missing, this script says so and falls
back to opening the default browser instead of failing.
"""

from __future__ import annotations

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import server as srv  # noqa: E402
from ensys import __version__  # noqa: E402


def start_server(port):
    from http.server import ThreadingHTTPServer

    httpd = ThreadingHTTPServer(("127.0.0.1", port), srv.Handler)
    httpd.verbose = False
    httpd.started = time.time()
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def main():
    port = srv.find_free_port(8756, "127.0.0.1")
    httpd = start_server(port)
    url = f"http://127.0.0.1:{port}/"

    try:
        import webview
    except ImportError:
        print(
            f"\n  pywebview is not installed, so the native window is not\n"
            f"  available. Opening your browser instead.\n\n"
            f"    pip install pywebview     # to get the desktop window\n\n"
            f"  HES {__version__} is running at {url}\n"
            f"  Ctrl+C to stop.\n"
        )
        import webbrowser

        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n  Stopped.\n")
        finally:
            httpd.shutdown()
        return

    webview.create_window(
        f"HES {__version__} — Hybrid Energy System Sizing",
        url, width=1360, height=900, min_size=(980, 640),
    )
    try:
        webview.start()
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
