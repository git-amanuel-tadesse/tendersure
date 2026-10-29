"""The Flask app: the API under /api and the built web app at /.

Local first (spec 06 W2): the server only listens on 127.0.0.1, and every
request is checked here as well, because a page on another site can still
reach a local port through the browser:

- the Host must be this machine (a DNS-rebinding page arrives with its own
  domain as the Host);
- an Origin, when the browser sends one, must be this machine too;
- every request that changes something carries `X-TenderSure: 1`. A page on
  another site cannot add that header without a CORS preflight, and this
  server never answers one.

Errors are always JSON: {"error": "...", "detail": ...}.
"""

import os
from typing import Optional
from urllib.parse import urlsplit

from flask import abort, jsonify, request, send_from_directory
from flask_openapi3 import Info, OpenAPI
from pydantic import BaseModel
from werkzeug.exceptions import HTTPException

from tendersure import flows

LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
HEADER = "X-TenderSure"
VERSION = "0.1.0"
WEB_DIST = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "web", "dist"))


class Health(BaseModel):
    ok: bool
    version: str


def _host(netloc: str) -> str:
    """'localhost:5000' -> 'localhost', '[::1]:5000' -> '[::1]'."""
    if netloc.startswith("["):
        return netloc[:netloc.find("]") + 1]
    return netloc.rsplit(":", 1)[0] if netloc.count(":") == 1 else netloc


def _invalid(e):
    """A request that does not match its schema: the same JSON shape as every other error."""
    r = jsonify(error="Invalid request", detail=e.errors(include_url=False, include_context=False))
    r.status_code = 422
    return r                             # flask-openapi3 aborts with this response as it is


def create_app(web_dist: Optional[str] = WEB_DIST, workspaces: Optional[str] = None) -> OpenAPI:
    from .bank import bp as bank
    from .exercises import MAX_UPLOAD, bp as exercises
    from .jobs import Runner
    from .jobs_api import bp as jobs
    from .workspaces import Workspaces
    app = OpenAPI(__name__, info=Info(title="TenderSure", version=VERSION), doc_ui=False, static_folder=None,
                  validation_error_callback=_invalid)
    app.json.sort_keys = False
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD
    app.config["WORKSPACES"] = store = Workspaces(workspaces)
    app.config["JOBS"] = runner = Runner(os.path.join(store.root, ".jobs"))

    # a request's own log lines are not a running job's progress
    @app.before_request
    def serving():
        runner.serving(True)

    @app.teardown_request
    def served(_exc):
        runner.serving(False)

    @app.before_request
    def local_only():
        if _host(request.host).lower() not in LOCAL_HOSTS:
            abort(403, description="this server only answers requests addressed to this machine")
        origin = request.headers.get("Origin")
        if origin and _host(urlsplit(origin).netloc).lower() not in LOCAL_HOSTS:
            abort(403, description="requests from other sites are refused")
        if request.method not in SAFE_METHODS and request.headers.get(HEADER) != "1":
            abort(403, description=f"a request that changes something must carry the {HEADER}: 1 header")

    @app.errorhandler(HTTPException)
    def http_error(e: HTTPException):
        if e.response is not None:       # already a full JSON response (an invalid request)
            return e.response
        return jsonify(error=e.name, detail=e.description), e.code

    @app.errorhandler(flows.FlowError)
    def flow_error(e: flows.FlowError):
        return jsonify(error="Cannot continue", detail=str(e)), 422

    @app.errorhandler(Exception)
    def unexpected(e: Exception):
        app.logger.exception("unexpected error")
        return jsonify(error="Internal error", detail=f"{type(e).__name__}: {e}"), 500

    @app.get("/api/health", responses={200: Health})
    def health():
        return Health(ok=True, version=VERSION).model_dump()

    app.register_api(exercises)
    app.register_api(jobs)
    app.register_api(bank)

    # Plain Flask routes, outside the OpenAPI document: the document itself,
    # and the built web app (apps/web/dist) - its files, and index.html for
    # every other path outside /api, so the browser's routes work on reload.
    def openapi():
        return jsonify(app.api_doc)

    def web(path: str):
        if path == "api" or path.startswith("api/"):
            abort(404, description=f"no API route /{path}")
        if not web_dist or not os.path.isdir(web_dist):
            abort(404, description="the web app is not built: run `make build`, or `make dev` while working on it")
        if path and os.path.isfile(os.path.join(web_dist, path)):
            return send_from_directory(web_dist, path)
        return send_from_directory(web_dist, "index.html")

    app.add_url_rule("/api/openapi.json", "openapi", openapi, methods=["GET"])
    app.add_url_rule("/", "web_root", web, defaults={"path": ""}, methods=["GET"])
    app.add_url_rule("/<path:path>", "web", web, methods=["GET"])
    return app
