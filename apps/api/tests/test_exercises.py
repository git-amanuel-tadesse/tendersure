"""Spec 06 W3: exercises and their files, stored under workspaces/ and never outside it."""

import io
import os

import pytest

W = {"X-TenderSure": "1"}
ERCS = {"name": "ERCS 2026–2029", "client": "ercs", "prefix": "ERCS-ET-2026-", "client_name": "Ethiopian Red Cross"}


def _create(client, **kw):
    r = client.post("/api/exercises", json={**ERCS, **kw}, headers=W)
    assert r.status_code == 201, r.json
    return r.json


def _upload(client, eid, name, data=b"PK\x03\x04 bytes", **kw):
    return client.post(f"/api/exercises/{eid}/files", data={"file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data", headers=W, **kw)


def test_an_exercise_is_created_listed_and_read(client, workspaces):
    ex = _create(client)
    assert ex["id"] == "ercs-2026-2029" and ex["client"] == "ERCS" and ex["files"] == []
    assert os.path.isfile(workspaces / "ercs-2026-2029" / "exercise.json")
    again = _create(client)                                         # same name: a new folder, not the same one
    assert again["id"] == "ercs-2026-2029-2"
    listed = client.get("/api/exercises").json["exercises"]
    assert {e["id"] for e in listed} == {"ercs-2026-2029", "ercs-2026-2029-2"}
    assert client.get("/api/exercises/ercs-2026-2029").json["name"] == "ERCS 2026–2029"


def test_bad_exercise_input_is_refused_with_the_reason(client):
    r = client.post("/api/exercises", json={**ERCS, "client": "ER CS"}, headers=W)
    assert r.status_code == 400 and "letters and digits" in r.json["detail"]
    r = client.post("/api/exercises", json={"name": "x"}, headers=W)            # fields missing
    assert r.status_code == 422 and r.json["error"] == "Invalid request" and r.json["detail"]
    for eid in ("nope", "../etc", "ERCS"):
        assert client.get(f"/api/exercises/{eid}").status_code == 404


def test_upload_then_download_gives_the_same_bytes(client):
    eid = _create(client)["id"]
    data = bytes(range(256)) * 1000
    r = _upload(client, eid, "Red Cross Categories 1st Draft.xlsx", data)
    assert r.status_code == 201 and r.json["path"] == "uploads/Red Cross Categories 1st Draft.xlsx"
    assert r.json["size"] == len(data)
    got = client.get(f"/api/exercises/{eid}/files/uploads/Red Cross Categories 1st Draft.xlsx")
    assert got.status_code == 200 and got.data == data
    assert "Red Cross Categories 1st Draft.xlsx" in got.headers["Content-Disposition"]
    assert [f["path"] for f in client.get(f"/api/exercises/{eid}").json["files"]] == [r.json["path"]]


def test_an_upload_never_overwrites(client):
    eid = _create(client)["id"]
    assert _upload(client, eid, "draft.xlsx", b"one").json["path"] == "uploads/draft.xlsx"
    assert _upload(client, eid, "draft.xlsx", b"two").json["path"] == "uploads/draft (2).xlsx"
    assert client.get(f"/api/exercises/{eid}/files/uploads/draft.xlsx").data == b"one"


@pytest.mark.parametrize("name", ["../x.xlsx", "a/b.xlsx", "..\\x.xlsx", ".hidden.xlsx", "x..xlsx",
                                  "run.exe", "notes.txt", "", "a\x00.xlsx"])
def test_unsafe_file_names_are_refused(client, workspaces, name):
    eid = _create(client)["id"]
    r = _upload(client, eid, name)
    assert r.status_code in (400, 422), (name, r.json)
    assert not (workspaces / eid / "uploads").exists() or os.listdir(workspaces / eid / "uploads") == []
    assert not (workspaces / "x.xlsx").exists()


@pytest.mark.parametrize("rel", ["../exercise.json", "exercise.json", "uploads/../../other/exercise.json",
                                 "%2e%2e/%2e%2e/secret.xlsx", "uploads/missing.xlsx", "uploads"])
def test_nothing_outside_the_exercise_files_can_be_downloaded(client, workspaces, rel):
    eid = _create(client)["id"]
    other = _create(client, name="Other")["id"]
    (workspaces / "secret.xlsx").write_bytes(b"secret")
    _upload(client, eid, "a.xlsx")
    r = client.get(f"/api/exercises/{eid}/files/{rel}")
    assert r.status_code == 404 and r.is_json and other


def test_a_file_too_large_is_refused(client, monkeypatch):
    eid = _create(client)["id"]
    client.application.config["MAX_CONTENT_LENGTH"] = 1000
    r = _upload(client, eid, "big.xlsx", b"x" * 5000)
    assert r.status_code == 413 and r.is_json


def test_writes_still_need_the_header(client):
    assert client.post("/api/exercises", json=ERCS).status_code == 403


def test_the_default_store_is_in_the_project_home():
    from tendersure import flows
    from tendersure_api.workspaces import Workspaces
    assert Workspaces().root == os.path.realpath(os.path.join(flows.current().home, "workspaces"))


@pytest.mark.parametrize("name, eid", [("ERCS 2026–2029", "ercs-2026-2029"), ("NRC — 2027/28", "nrc-2027-28"),
                                       ("Éthiopie Café", "ethiopie-cafe"), ("  __ ", "exercise")])
def test_exercise_ids_are_readable_slugs(name, eid):
    from tendersure_api.workspaces import slug
    assert slug(name) == eid
