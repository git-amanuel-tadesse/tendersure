"""tendersure-api: the local server for the web app (spec 06 W2).

It listens on 127.0.0.1 only; there is no option to listen anywhere else.
"""

import argparse
import threading
import webbrowser

from .app import create_app

HOST = "127.0.0.1"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tendersure-api", description=__doc__)
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--no-browser", action="store_true", help="do not open the web app in a browser")
    ap.add_argument("--debug", action="store_true", help="reload on code changes (for `make dev`)")
    a = ap.parse_args(argv)
    if not a.no_browser:
        threading.Timer(1.0, webbrowser.open, [f"http://localhost:{a.port}/"]).start()
    create_app().run(host=HOST, port=a.port, debug=a.debug, use_reloader=a.debug)
