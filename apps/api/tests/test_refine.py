"""The client's draft in the browser (spec 18 A-C): one tab or several read as one, hidden tabs never offered;
reading a draft starts the category list from it (spec 21 A)."""

import io
import json
import os

import openpyxl
import pytest

W = {"X-TenderSure": "1"}
RC = [(None, "GENERAL"),
      (1, "Supply of office stationery"),
      (2, "Provision of Translation and Interpretation services"),
      ("ICT", None),
      (3, "Supply, installation and maintenance of ICT equipment (Servers, Routers)")]


def _book(rows=RC, tab="Draft list"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = tab
    wb.create_sheet("Notes")
    ws["B3"], ws["C3"] = "No", "Category Description"
    for i, (num, text) in enumerate(rows, 4):
        ws.cell(i, 2, num)
        ws.cell(i, 3, text)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _two_tabs():
    wb = openpyxl.Workbook()
    wb.active.title, wb.active.sheet_state = "CoverP", "hidden"
    for tab, rows in (("Goods", [(1, "Supply of office stationery"), (2, "Provision of taxi services")]),
                      ("Consultancy", [(1, "Research design"), (None, "– Baseline survey"), (2, "Statisticians")])):
        ws = wb.create_sheet(tab)
        ws.append(["serial", "List of Item Categories"])
        for row in rows:
            ws.append(list(row))
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


@pytest.fixture
def ex(client, home):
    r = client.post("/api/exercises", json={"name": "RC 2026", "client": "AAA", "prefix": "AAA-ET-2026-"}, headers=W)
    return r.json["id"]


def _upload(client, eid, name, data):
    r = client.post(f"/api/exercises/{eid}/files", data={"file": (io.BytesIO(data), name)},
                    content_type="multipart/form-data", headers=W)
    assert r.status_code == 201, r.json
    return r.json["path"]


def test_a_draft_is_read_and_starts_the_list(client, ex):
    f = _upload(client, ex, "Red Cross Draft.xlsx", _book())
    r = client.post(f"/api/exercises/{ex}/refine/draft", json={"file": f, "sheets": ["Draft list"]}, headers=W)
    assert r.status_code == 200, r.json
    d = r.json["draft"]
    assert d["tabs"] == ["Draft list", "Notes"] and [x["group"] for x in d["drafts"]] == ["GENERAL", "GENERAL", "ICT"]
    lst = client.get(f"/api/exercises/{ex}/refine/list").json
    assert lst["version"] == 1 and [r["description"] for r in lst["rows"]] == [
        "Supply of office stationery", "Provision of Translation and Interpretation services",
        "Supply, installation and maintenance of ICT equipment (Servers, Routers)"]       # as written


def test_several_tabs_are_one_draft_and_hidden_tabs_are_not_offered(client, ex):
    base = f"/api/exercises/{ex}/refine"
    f = _upload(client, ex, "Amref.xlsx", _two_tabs())
    r = client.post(f"{base}/draft", json={"file": f, "sheets": ["Goods", "Consultancy"]}, headers=W)
    assert r.status_code == 200, r.json
    d = r.json["draft"]
    assert d["tabs"] == ["Goods", "Consultancy"]
    assert [x["id"] for x in d["drafts"]] == ["1-1", "1-2", "2-1", "2-2"]
    assert d["drafts"][2]["sub_categories"] == ["Baseline survey"]
    assert client.post(f"{base}/draft", json={"file": f, "sheets": ["CoverP"]}, headers=W).status_code == 422


def test_wrong_requests_say_what_to_do(client, ex):
    base = f"/api/exercises/{ex}/refine"
    assert client.post(f"{base}/draft", json={"file": "uploads/none.xlsx"}, headers=W).status_code == 404
    f = _upload(client, ex, "draft.xlsx", _book())
    r = client.post(f"{base}/draft", json={"file": f, "sheets": ["Nope"]}, headers=W)
    assert r.status_code == 422 and "has no visible tab 'Nope'" in r.json["detail"]
    r = client.post(f"{base}/draft", json={"file": f, "sheets": ["Notes"]}, headers=W)          # a tab with no list
    assert r.status_code == 422
    assert client.post(f"{base}/draft", json={"file": f}).status_code == 403                  # no header


def test_an_earlier_refinement_moves_to_history(client, ex):
    f = _upload(client, ex, "Red Cross Draft.xlsx", _book())
    folder = client.application.config["WORKSPACES"].folder(ex)
    os.makedirs(os.path.join(folder, "refine"), exist_ok=True)
    with open(os.path.join(folder, "refine", "draft.json"), "w") as fh:          # written before several tabs
        json.dump({"file": f, "sheet": "Draft list", "tabs": ["Draft list", "Notes"], "drafts": []}, fh)
    with open(os.path.join(folder, "refine", "revised.json"), "w") as fh:        # an AI proposal before spec 21
        json.dump({"lines": []}, fh)
    assert client.get(f"/api/exercises/{ex}/refine").json["draft"]["sheets"] == ["Draft list"]
    client.post(f"/api/exercises/{ex}/refine/draft", json={"file": f, "sheets": ["Draft list"]}, headers=W)
    history = os.path.join(folder, "refine", "history")
    assert any(os.path.exists(os.path.join(history, d, "revised.json")) for d in os.listdir(history))
    assert not os.path.exists(os.path.join(folder, "refine", "revised.json"))
