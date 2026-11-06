"""tendersure-api: the server for the web app (spec 06 W2, spec 20).

By default it is local: it listens on 127.0.0.1 only. `--serve` runs it as
the team's server (Render): under waitress, on every address, on $PORT. It
refuses to unless TENDERSURE_PASSWORD is set, so a public server always asks
for the team's password (see app.py).
The default port is 8765, not 5000: on macOS the AirPlay Receiver holds 5000
on every address, so http://localhost:5000 (which tries IPv6 ::1 first)
reaches AirPlay and gets an empty 403.
"""

import argparse
import logging
import os
import socket
import sys
import threading
import webbrowser

from .app import create_app

HOST = "127.0.0.1"
PORT = 8765
MIN_PASSWORD = 16
THREADS = 8


def taken(port: int) -> list:
    """The local addresses where another program already answers on `port`."""
    out = []
    for family, addr in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(0.3)
                if s.connect_ex((addr, port)) == 0:
                    out.append(addr)
        except OSError:
            continue                        # no IPv6 here: nothing can listen on it either
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tendersure-api", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-browser", action="store_true", help="do not open the web app in a browser")
    ap.add_argument("--debug", action="store_true", help="reload on code changes (for `make dev`)")
    ap.add_argument("--serve", action="store_true",
                    help="the team's server: waitress on 0.0.0.0:$PORT, behind TENDERSURE_PASSWORD")
    a = ap.parse_args(argv)
    if a.serve:
        return serve()
    busy = taken(a.port)
    if busy:
        # macOS lets a server bind 127.0.0.1:<port> beside another program holding the same port on
        # every address; the browser then reaches one or the other. Refuse instead of half-working.
        sys.exit(f"port {a.port} is already used by another program (on {', '.join(busy)}): "
                 f"start with --port <another>, e.g. --port {a.port + 1}")
    url = f"http://{HOST}:{a.port}/"
    print(f"TenderSure: {url}")
    if not a.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    # .env is read by tendersure itself (paths, the privacy-checked model settings), never by Flask
    create_app().run(host=HOST, port=a.port, debug=a.debug, use_reloader=a.debug, load_dotenv=False)


def serve():
    """One process with several request threads, so the in-process job worker stays the only one."""
    password = os.environ.get("TENDERSURE_PASSWORD") or ""
    if len(password) < MIN_PASSWORD:
        sys.exit(f"--serve answers on every address: set TENDERSURE_PASSWORD (at least {MIN_PASSWORD} "
                 "characters) first, so only the team gets in")
    from waitress import serve as waitress_serve
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    port = int(os.environ.get("PORT") or PORT)
    print(f"TenderSure team server on 0.0.0.0:{port}", flush=True)
    waitress_serve(create_app(), host="0.0.0.0", port=port, threads=THREADS, ident="TenderSure")
