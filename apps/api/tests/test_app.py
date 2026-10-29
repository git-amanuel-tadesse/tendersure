"""Spec 06 W2: local only, the header on every write, JSON errors, the web app at /."""

import pytest

from tendersure import flows

W = {"X-TenderSure": "1"}


def test_health_answers_on_this_machine(client):
    for host in ("localhost:5000", "127.0.0.1:5000", "[::1]:5000", "localhost"):
        r = client.get("/api/health", headers={"Host": host})
        assert r.status_code == 200 and r.json == {"ok": True, "version": "0.1.0"}


@pytest.mark.parametrize("host", ["evil.example", "evil.example:5000", "localhost.evil.example", "192.168.1.5:5000"])
def test_a_request_addressed_to_another_host_is_refused(client, host):
    r = client.get("/api/health", headers={"Host": host})               # DNS rebinding arrives like this
    assert r.status_code == 403 and r.json["error"] == "Forbidden"


def test_a_request_from_another_site_is_refused(client):
    r = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.get("/api/health", headers={"Origin": "http://localhost:5173"}).status_code == 200   # vite dev


def test_a_write_without_the_header_is_refused(client):
    r = client.post("/api/health")
    assert r.status_code == 403 and "X-TenderSure: 1" in r.json["detail"]
    assert client.post("/api/health", headers={"X-TenderSure": "yes"}).status_code == 403
    assert client.post("/api/health", headers=W).status_code == 405            # past the guard: no such method
    assert client.delete("/api/health").status_code == 403


def test_errors_are_json(client):
    r = client.get("/api/nothing-here")
    assert r.status_code == 404 and r.is_json and set(r.json) == {"error", "detail"}
    r = client.post("/api/health", headers=W)
    assert r.status_code == 405 and r.json["error"] == "Method Not Allowed"


def test_a_flow_that_cannot_go_on_is_a_422_with_its_message(web_dist):
    from tendersure_api import create_app
    app = create_app(web_dist=str(web_dist))

    @app.get("/api/test-flow-error")
    def fails():
        raise flows.FlowError("no bank at data/bank.sqlite")
    r = app.test_client().get("/api/test-flow-error")
    assert r.status_code == 422 and r.json == {"error": "Cannot continue", "detail": "no bank at data/bank.sqlite"}


def test_the_web_app_is_served_with_its_routes(client):
    assert client.get("/").status_code == 200 and b"TenderSure" in client.get("/").data
    assert client.get("/assets/app.js").data == b"console.log('app')"
    assert b"TenderSure" in client.get("/exercises/ercs-2026/refine").data      # a browser route, reloaded
    assert client.get("/api/unknown").status_code == 404                        # /api never falls back


def test_without_a_build_the_page_says_how_to_get_one():
    from tendersure_api import create_app
    r = create_app(web_dist="/nonexistent").test_client().get("/")
    assert r.status_code == 404 and "make build" in r.json["detail"]


def test_the_openapi_document_lists_the_api(client):
    doc = client.get("/api/openapi.json").json
    assert doc["info"]["title"] == "TenderSure" and "/api/health" in doc["paths"]
    assert all(p.startswith("/api/") for p in doc["paths"])                    # the web routes are not in it


def test_the_server_listens_on_this_machine_only(monkeypatch):
    from tendersure_api import server
    seen = {}
    monkeypatch.setattr("flask.Flask.run", lambda self, **kw: seen.update(kw))
    server.main(["--no-browser", "--port", "5055"])
    assert seen["host"] == "127.0.0.1" and seen["port"] == 5055


def test_every_model_the_api_uses_has_a_name_of_its_own():
    """OpenAPI names a schema by its class: two models called Import would share one schema, and the web app's
    generated types would be wrong for one of the endpoints."""
    import importlib
    import inspect
    import pkgutil

    import tendersure_api
    from pydantic import BaseModel
    seen = {}
    for m in pkgutil.iter_modules(tendersure_api.__path__):
        mod = importlib.import_module(f"tendersure_api.{m.name}")
        for name, cls in inspect.getmembers(mod, inspect.isclass):
            if issubclass(cls, BaseModel) and cls.__module__ == mod.__name__:
                seen.setdefault(name, set()).add(mod.__name__)
    assert {n: sorted(ms) for n, ms in seen.items() if len(ms) > 1} == {}


def test_the_server_refuses_a_port_another_program_holds(monkeypatch):
    """On macOS the AirPlay Receiver holds 5000; binding 127.0.0.1 beside it works, but localhost then reaches
    AirPlay. A port anyone else answers on is refused, with the way out."""
    import socket

    from tendersure_api import server
    monkeypatch.setattr("flask.Flask.run", lambda self, **kw: None)
    with socket.socket() as other:
        other.bind(("127.0.0.1", 0))
        other.listen()
        port = other.getsockname()[1]
        assert server.taken(port) == ["127.0.0.1"]
        with pytest.raises(SystemExit) as e:
            server.main(["--no-browser", "--port", str(port)])
        assert f"port {port} is already used by another program" in str(e.value) and "--port" in str(e.value)


def test_the_default_port_is_not_5000():
    from tendersure_api import server
    assert server.PORT == 8765
