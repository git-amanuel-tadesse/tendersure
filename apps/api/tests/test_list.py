"""Spec 21: the category list shaped by the team's comments, in the browser's API. The AI step is a stand-in
that answers from a script: these tests are about the server's rules (one waiting comment, re-reading after a
change, versions, undo, outputs), not the model."""

import io
import os

import openpyxl
import pytest

from tendersure import flows

W = {"X-TenderSure": "1"}


def _book():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Goods"
    ws.append(["serial", "List of Item Categories"])
    for row in [(1, "Supply of general stationeries and tonners"), (2, "Provision of event management services"),
                (3, "Rapporteur and Facilitation"), (4, "Provision of photo and video licensing")]:
        ws.append(list(row))
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


@pytest.fixture
def ex(client, home):
    r = client.post("/api/exercises", json={"name": "Amref", "client": "AAA", "prefix": "AAA-ET-2026-"}, headers=W)
    return r.json["id"]


@pytest.fixture
def ai(monkeypatch):
    """The AI's answers, one per comment read, in order."""
    script = []

    def read_comment(text, rows, drafts, refs, client, brief="", model=None):
        keys = [r.key for r in rows]
        C, I = flows.ListChange, flows.ListInstruction
        answer = script.pop(0)(keys)
        return flows.ListReading(answer[0], answer[1], answer[2] if len(answer) > 2 else "", {"first": {"ok": 1}})
    monkeypatch.setattr(flows, "read_comment", read_comment)
    return script


def _upload(client, eid, name, data):
    r = client.post(f"/api/exercises/{eid}/files", data={"file": (io.BytesIO(data), name)},
                    content_type="multipart/form-data", headers=W)
    assert r.status_code == 201, r.json
    return r.json["path"]


def _wait(client, r):
    assert r.status_code == 202, r.json
    job = client.application.config["JOBS"].wait(r.json["id"])
    return job


def _comment(client, base, text):
    return _wait(client, client.post(f"{base}/comment", json={"text": text, "author": "Hana"}, headers=W))


def test_comments_shape_the_list_one_at_a_time(client, ex, ai):
    base = f"/api/exercises/{ex}/refine/list"
    f = _upload(client, ex, "Amref.xlsx", _book())
    assert client.post(f"/api/exercises/{ex}/refine/draft", json={"file": f, "sheets": []}, headers=W).status_code == 200
    s = client.get(base).json
    assert s["version"] == 1 and [r["description"] for r in s["rows"]][0] == "Supply of general stationeries and tonners"

    C, I = flows.ListChange, flows.ListInstruction
    ai.append(lambda keys: ([C("fold", id="c1", key="3", target="2", instruction=0),
                             C("remove", id="c2", key="4", instruction=1, risky="looks risky")],
                            [I("Put rapporteur under events", "done", "", ["3"]), I("Remove licensing", "done", "", ["4"])]))
    job = _comment(client, base, "Put rapporteur under events. Remove photo and video licensing.")
    assert job.status == "done", job.error
    p = client.get(base).json["pending"]
    assert p["state"] == "ready" and p["author"] == "Hana" and [c["ticked"] for c in p["changes"]] == [True, False]

    r = client.post(f"{base}/comment", json={"text": "another"}, headers=W)            # one waits at a time
    assert r.status_code == 409 and "decide on the last comment first" in r.json["detail"]

    s = client.post(f"{base}/decide", json={"id": p["id"], "apply": True, "ticked": ["c1"]}, headers=W).json
    assert s["version"] == 2 and s["pending"] is None and len(s["rows"]) == 3            # licensing kept: unticked
    assert s["rows"][1]["subs"] == ["Rapporteur and Facilitation"] and s["thread"][0]["state"] == "applied"

    # a comment read on version 2, then the list changes (undo): it must be read again before applying
    ai.append(lambda keys: ([C("reword", id="c1", key="1", text="Supply of Office Stationery", instruction=0)],
                            [I("PSI's wording for stationery", "done", "", ["1"])]))
    _comment(client, base, "Use PSI's wording for stationery")
    p = client.get(base).json["pending"]
    s = client.post(f"{base}/undo", json={}, headers=W).json
    assert s["version"] == 3 and len(s["rows"]) == 4 and s["thread"][0]["state"] == "undone"
    r = client.post(f"{base}/decide", json={"id": p["id"], "apply": True, "ticked": ["c1"]}, headers=W)
    assert r.status_code == 409 and "read it again" in r.json["detail"]
    ai.append(lambda keys: ([C("reword", id="c1", key="1", text="Supply of Office Stationery", instruction=0)],
                            [I("PSI's wording for stationery", "done", "", ["1"])]))
    _wait(client, client.post(f"{base}/reread", json={"id": p["id"]}, headers=W))
    s = client.post(f"{base}/decide", json={"id": p["id"], "apply": True, "ticked": ["c1"]}, headers=W).json
    assert s["version"] == 4 and s["rows"][0]["description"] == "Supply of Office Stationery"
    assert client.post(f"{base}/undo", json={}, headers=W).status_code == 200
    assert client.post(f"{base}/undo", json={}, headers=W).status_code == 409           # only the latest

    # rejecting keeps the list; everything is in the log
    ai.append(lambda keys: ([], [I("unclear", "unclear", "which ones?")]))
    _comment(client, base, "fix the others")
    p = client.get(base).json["pending"]
    s = client.post(f"{base}/decide", json={"id": p["id"], "apply": False}, headers=W).json
    assert s["version"] == 5 and s["thread"][-1]["state"] == "rejected"
    folder = client.application.config["WORKSPACES"].folder(ex)
    events = [line for line in open(os.path.join(folder, "refine", "comments.jsonl"))]
    assert sum('"event": "read"' in e for e in events) == 4


def test_outputs_check_and_a_new_draft(client, ex, ai):
    base = f"/api/exercises/{ex}/refine/list"
    f = _upload(client, ex, "Amref.xlsx", _book())
    client.post(f"/api/exercises/{ex}/refine/draft", json={"file": f, "sheets": []}, headers=W)
    r = client.get(f"{base}/client.xlsx")
    assert r.status_code == 200 and "Amref - categories.xlsx" in r.headers["Content-Disposition"]
    ws = openpyxl.load_workbook(io.BytesIO(r.data)).active
    assert [c.value for c in ws[1]] == ["#", "Category Description", "Sub-Category"]

    r = client.post(f"{base}/categories", json={"version": "v1"}, headers=W)
    assert r.status_code == 200 and r.json["count"] == 4
    cats = flows.load_categories(os.path.join(client.application.config["WORKSPACES"].folder(ex), "categories.csv"))
    assert [c.description for c in cats][0] == "Supply of general stationeries and tonners"

    team = _upload(client, ex, "Team.xlsx", _book())
    got = client.post(f"{base}/check", json={"file": team}, headers=W).json
    assert (got["target"], got["same_wording"], got["missing"], got["extra"]) == (4, 4, [], [])

    # a draft read again starts a new list; the old one is kept in history
    client.post(f"/api/exercises/{ex}/refine/draft", json={"file": f, "sheets": []}, headers=W)
    folder = client.application.config["WORKSPACES"].folder(ex)
    assert client.get(base).json["version"] == 1
    assert any(os.path.isdir(os.path.join(folder, "refine", "history", d, "list"))
               for d in os.listdir(os.path.join(folder, "refine", "history")))


def test_comments_need_a_draft_and_the_ai(client, ex, monkeypatch):
    def ai_off(*a, **kw):                                                               # never the real model here
        raise flows.FlowError("comments need the AI, and it is off: no key")
    monkeypatch.setattr(flows, "read_comment", ai_off)
    base = f"/api/exercises/{ex}/refine/list"
    assert client.get(base).status_code == 409                                          # no draft yet
    f = _upload(client, ex, "Amref.xlsx", _book())
    client.post(f"/api/exercises/{ex}/refine/draft", json={"file": f, "sheets": []}, headers=W)
    job = _comment(client, base, "Keep the client's wording")
    assert job.status == "failed"
    p = client.get(base).json["pending"]
    assert p["state"] == "failed" and "AI" in p["note"]
    r = client.post(f"{base}/decide", json={"id": p["id"], "apply": True}, headers=W)
    assert r.status_code == 409
    assert client.post(f"{base}/decide", json={"id": p["id"], "apply": False}, headers=W).status_code == 200
