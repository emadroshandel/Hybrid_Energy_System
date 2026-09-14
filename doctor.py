#!/usr/bin/env python3
"""
Self-check.

Run this when the application will not start. It reports what is wrong in
words rather than a traceback, because a stack trace in a console window
that closes itself helps nobody.

    python doctor.py            # full report
    python doctor.py --quiet    # only problems; exit code 1 if any
"""

from __future__ import annotations

import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

OK, WARN, FAIL = "ok", "warn", "fail"
_results = []
VERSION = "?"


def probe_health(port, timeout=1.5):
    """Ask whatever is listening on this port to identify itself."""
    import json
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health", timeout=timeout
        ) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def check(name, status, detail="", fix=""):
    _results.append((name, status, detail, fix))


def run_checks():
    # ------------------------------------------------------ interpreter
    v = sys.version_info
    if v < (3, 9):
        check("Python version", FAIL,
              f"Python {v.major}.{v.minor}.{v.micro} is too old.",
              "Install Python 3.9 or newer from python.org.")
    else:
        check("Python version", OK, f"{v.major}.{v.minor}.{v.micro}")

    # -------------------------------------------------------- location
    if not os.path.isfile(os.path.join(HERE, "server.py")):
        check("Project folder", FAIL,
              f"server.py is not in {HERE}",
              "Run this from inside the EnergyManagement folder.")
        return
    check("Project folder", OK, HERE)

    # ---------------------------------------------------- engine files
    sys.path.insert(0, HERE)
    missing = []
    for rel in ("ensys/__init__.py", "ensys/api.py", "ensys/dispatch.py",
                "ensys/assets.py", "ensys/catalogue.py",
                "ensys/diagram/sld.py", "ensys/diagram/sheet.py",
                "ensys/diagram/topology.py", "ensys/sizing/design.py",
                "web/index.html", "web/app.js", "web/boot.js",
                "web/charts.js", "web/style.css"):
        if not os.path.isfile(os.path.join(HERE, rel.replace("/", os.sep))):
            missing.append(rel)
    if missing:
        check("Engine files", FAIL,
              f"{len(missing)} file(s) missing: {', '.join(missing[:5])}"
              + (" ..." if len(missing) > 5 else ""),
              "Re-copy the project folder; it is incomplete.")
    else:
        check("Engine files", OK, "all present")

    # ----------------------------------------------------- can it import
    try:
        from ensys import __version__, api  # noqa: F401
        globals()["VERSION"] = __version__
        check("Engine import", OK, f"HES {__version__}")
    except Exception as e:
        check("Engine import", FAIL, f"{type(e).__name__}: {e}",
              "The engine could not be loaded. The error above names the "
              "module at fault.")
        return

    # ------------------------------------------------------ does it run
    try:
        r = api.handle("info", {})
        if r.get("ok"):
            n = sum(len(v) for v in
                    r["presets"]["technologies"]["groups"].values())
            check("Engine self-test", OK, f"{n} technologies available")
        else:
            check("Engine self-test", FAIL, r.get("error", "unknown error"))
    except Exception as e:
        check("Engine self-test", FAIL, f"{type(e).__name__}: {e}")

    # ------------------------------------------- something already listening
    # A server left running from an earlier session, or from a copy of the
    # project in another folder, answers on this port and the browser talks
    # to it instead of the one just started. The two can be different
    # versions, and then nothing behaves as expected for a reason that is
    # invisible from the interface.
    other = probe_health(8756)
    if other is None:
        check("Port 8756", OK, "nothing else is using it")
    elif other.get("root") and os.path.normcase(
            os.path.abspath(other["root"])) != os.path.normcase(HERE):
        check("Port 8756", FAIL,
              f"An HES server is already running on port 8756 from a "
              f"different folder: {other['root']} "
              f"(version {other.get('version', '?')}, "
              f"pid {other.get('pid', '?')}).",
              "Close that window first, or run this copy on another port: "
              "python server.py --port 8760")
    elif other.get("version") not in (VERSION, None):
        check("Port 8756", FAIL,
              f"An HES {other.get('version')} server is already running "
              f"(pid {other.get('pid', '?')}); this copy is {VERSION}.",
              "Close that window so the interface and the engine match.")
    else:
        check("Port 8756", WARN,
              f"An HES server is already running "
              f"(pid {other.get('pid', '?')}).",
              "Open http://127.0.0.1:8756/ rather than starting a second.")

    # ------------------------------------------------------------ port
    port_free = None
    for p in range(8756, 8776):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", p))
            s.close()
            port_free = p
            break
        except OSError:
            continue
    if port_free is None:
        check("Network port", FAIL,
              "No free port between 8756 and 8775.",
              "Something else is using them. Pass --port to choose another.")
    elif port_free != 8756:
        check("Network port", WARN,
              f"8756 is busy; the server will use {port_free}.",
              f"Open http://127.0.0.1:{port_free}/ rather than 8756.")
    else:
        check("Network port", OK, "8756 available")

    # ------------------------------------------------------- optionals
    try:
        import webview  # noqa: F401
        check("Desktop window (pywebview)", OK, "available")
    except ImportError:
        check("Desktop window (pywebview)", WARN,
              "not installed - desktop.py will open your browser instead.",
              "Optional. 'pip install pywebview' for a native window.")

    try:
        import numpy  # noqa: F401
        check("numpy (optional accelerator)", OK, "available")
    except ImportError:
        check("numpy (optional accelerator)", OK,
              "not installed - the engine runs in pure Python, as designed.")

    # -------------------------------------------------- browser opening
    try:
        import webbrowser
        if webbrowser.get():
            check("Browser launch", OK, "a browser is registered")
    except Exception:
        check("Browser launch", WARN,
              "Python cannot find a browser to open automatically.",
              "Not fatal: open the printed address yourself.")


def report(quiet=False):
    fails = [r for r in _results if r[1] == FAIL]
    warns = [r for r in _results if r[1] == WARN]

    if not quiet:
        print()
        print("  HES self-check")
        print("  " + "=" * 58)
        for name, status, detail, fix in _results:
            mark = {"ok": "[ok]  ", "warn": "[!]   ", "fail": "[X]   "}[status]
            print(f"  {mark}{name}")
            if detail:
                print(f"        {detail}")
            if fix and status != OK:
                print(f"        -> {fix}")
        print("  " + "=" * 58)

    if fails:
        if quiet:
            print()
            for name, _s, detail, fix in fails:
                print(f"  [X] {name}: {detail}")
                if fix:
                    print(f"      -> {fix}")
        else:
            print(f"  {len(fails)} problem(s) must be fixed before the "
                  f"application will start.")
        return 1

    if not quiet:
        if warns:
            print(f"  Ready to run, with {len(warns)} note(s) above.")
        else:
            print("  Everything checks out. Run:  python server.py")
        print()
    return 0


if __name__ == "__main__":
    quiet = "--quiet" in sys.argv
    run_checks()
    sys.exit(report(quiet))
