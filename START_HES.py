#!/usr/bin/env python3
"""
HES — double-click starter.

Use this if the .bat launchers do not run on your system; some security
software blocks batch files outright. Double-click this file, or from a
command prompt in this folder run:

    python START_HES.py
    python START_HES.py --desktop

Nothing is installed unless you ask for the desktop window: the engine runs
on the Python standard library alone.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)


def ensure(pkg, importname=None):
    """Import a package, installing it first if it is missing."""
    try:
        __import__(importname or pkg)
        return True
    except ImportError:
        print(f"  Installing {pkg} ...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
            return True
        except Exception as exc:
            print(f"  could not install {pkg}: {exc}")
            return False


def main():
    print("=" * 62)
    print("  HES - Hybrid Renewable Energy System Sizing")
    print("  Python:", sys.version.split()[0], "-", sys.executable)
    print("  Folder:", HERE)
    print("=" * 62)

    if sys.version_info < (3, 9):
        print("\n  Python 3.9 or newer is required.")
        input("\n  Press Enter to close...")
        return 1

    if not os.path.exists(os.path.join(HERE, "ensys", "__init__.py")):
        print("\n  The 'ensys' engine folder is missing or incomplete.")
        print("  Run this file from inside the HES folder.")
        input("\n  Press Enter to close...")
        return 1

    # The self-check names a broken installation instead of letting the
    # server fail later with something less informative.
    try:
        import doctor

        if doctor.report(quiet=True) != 0:
            print("\n  The self-check found a problem. Details are above.")
            input("\n  Press Enter to close...")
            return 1
    except Exception:
        pass  # the self-check is a courtesy, never a gate

    desktop = "--desktop" in sys.argv
    try:
        if desktop:
            ensure("pywebview", "webview")
            import desktop as app

            app.main()
        else:
            import server

            server.main([])
    except KeyboardInterrupt:
        pass
    except Exception:
        import traceback

        traceback.print_exc()
        input("\n  Something went wrong. Press Enter to close...")
        return 1

    input("\n  Stopped. Press Enter to close...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
